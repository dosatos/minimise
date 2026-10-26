"""End-to-end LoopEngine tests driven by a deterministic STUB harness.

No LLM, no network: the StubHarness returns canned control lines keyed by
(step_type, iteration, dimension) — read off the engine's own log_fields — so
every branch of the plan→implement→evaluate orchestrator is exercised
deterministically.
"""

import dataclasses
import importlib
import json
import re
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from minimise.agents.harness import AgentHarness, ClaudeCodeHarness, HarnessFactory, HarnessResult
from minimise.models import JobStatus, LoopSpec, TaskStatus, TokenUsage, Worker
from minimise.orchestration import loop_journal as journal
from minimise.orchestration.loop_engine import LoopEngine, _latest_valid_plan
from minimise.personas import Persona
from minimise.storage.database import Database
from minimise.storage.loop_store import LoopStore

SPEC = {
    "version": "1", "name": "Demo", "goal": "make it better", "max_iterations": 3,
    "loop": {
        "plan": {"prompt": "plan it"}, "implement": {"prompt": "do it"},
        "evaluate": {"max_concurrent": 2, "dimensions": [
            {"name": "a", "rubric": "ra"}, {"name": "b", "rubric": "rb"}]},
    },
}


class StubHarness(AgentHarness):
    """Route each call by (step_type, iteration, dimension) from log_fields.

    `script` maps that key -> a list of canned outputs consumed in order (the
    last repeats once exhausted, so a key hit again just re-emits it); an output
    that is already a HarnessResult is returned as is. A missing
    key falls back to `default`. `on_call(key)` runs before returning, so a
    scenario can flip external state (e.g. loops.status) mid-run. Every routed
    key is recorded in `calls` for assertions.
    """

    def __init__(self, script, default='{"control":"done"}', on_call=None):
        self.script = {k: list(v) for k, v in script.items()}
        self._idx = {k: 0 for k in self.script}
        self.default = default
        self.on_call = on_call
        self.calls = []

    def run(self, prompt, **kw):
        f = kw.get("log_fields") or {}
        key = (f.get("step_type"), f.get("iteration"), f.get("dimension"))
        self.calls.append(key)
        if self.on_call:
            self.on_call(key)
        outs = self.script.get(key)
        if outs is None:
            return HarnessResult(success=True, output=self.default)
        i = min(self._idx[key], len(outs) - 1)
        self._idx[key] += 1
        out = outs[i]
        return out if isinstance(out, HarnessResult) else HarnessResult(success=True, output=out)


class _FixedHarnessFactory(HarnessFactory):
    """Route every step to one pre-built harness, while still resolving models
    and system prompts through the real worker > persona > default chain."""

    def __init__(self, harness: AgentHarness, personas: dict):
        super().__init__(personas=personas)
        self._fixed = harness

    def _instantiate(self, name: str, model=None) -> AgentHarness:
        return self._fixed


def _build(tmp_path, spec=SPEC):
    db = Database(tmp_path / "t.db")
    db.init_db()
    store = LoopStore(db, tmp_path / "jobs")
    loop = store.create(LoopSpec.model_validate(spec), "example.yaml")
    return db, store, loop.loop_id


def _fixed(harness, personas=None):
    return _FixedHarnessFactory(harness, personas or {})


def _run(tmp_path, harness, spec=SPEC):
    db, store, lid = _build(tmp_path, spec)
    status = LoopEngine(factory=_fixed(harness), store=store, db=db).run(lid)
    return status, db, store, lid


# --- 1) planner stops on iteration 2 -> COMPLETED -------------------------------

def test_planner_stops_completed(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue","plan":"go"}'],
        ("implement", 1, None): ['{"control":"done"}'],
        ("evaluate", 1, "a"): ['{"control":"done","findings":"f"}'],
        ("evaluate", 1, "b"): ['{"control":"done","findings":"f"}'],
        ("plan", 2, None): ['{"control":"done"}'],
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    assert db.get_loop(lid).status == JobStatus.COMPLETED
    assert journal.last_committed_iteration(store.journal_path(lid)) == 1


# --- 2) planner never stops -> max_iterations backstop -> FAILED ----------------

def test_max_iterations_backstop_failed(tmp_path):
    # Every step keys default to "done"/"continue"; plan always continues.
    h = StubHarness({
        ("plan", i, None): ['{"control":"continue"}'] for i in (1, 2, 3)
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.FAILED
    assert db.get_loop(lid).status == JobStatus.FAILED
    assert journal.last_committed_iteration(store.journal_path(lid)) == 3


# --- 3) implement fails once -> inner loop back to plan, evaluate skipped -------

def test_implement_fail_reruns_plan_skips_evaluate(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue"}', '{"control":"done"}'],
        ("implement", 1, None): ['{"control":"failed","handover":"stuck"}'],
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    # The failing pass never reached evaluate, so no commit marker for iter 1.
    assert journal.last_committed_iteration(store.journal_path(lid)) == 0
    assert ("evaluate", 1, "a") not in h.calls and ("evaluate", 1, "b") not in h.calls
    # plan ran twice (initial + re-plan), implement once.
    assert h.calls.count(("plan", 1, None)) == 2
    assert h.calls.count(("implement", 1, None)) == 1


# --- 3b) implement always fails, planner always continues -> MAX_REPLANS cap ----

def test_replan_cap_exhausted_failed(tmp_path):
    """Without MAX_REPLANS this would spin forever: iteration never advances."""
    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue"}'],           # always continue
        ("implement", 1, None): ['{"control":"failed","handover":"stuck"}'],  # always fail
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.FAILED
    # never reached evaluate/commit — the failing pass never converges
    assert journal.last_committed_iteration(store.journal_path(lid)) == 0
    assert h.calls.count(("implement", 1, None)) == LoopEngine.MAX_REPLANS + 1


# --- 4a) malformed control -> gate re-runs the step -> succeeds within retries --

def test_malformed_control_retried_then_succeeds(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['not json at all', '{"control":"done"}'],  # retry -> valid
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    assert h.calls.count(("plan", 1, None)) == 2  # one reject + one accept


def test_pretty_printed_control_object_passes_the_gate_without_a_retry(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['Planned it.\n{\n  "control": "done",\n  "summary": "stop here"\n}\n'],
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    assert h.calls.count(("plan", 1, None)) == 1
    (step,) = [s for s in db.list_loop_steps(lid) if s.step_type == "plan"]
    assert step.status == TaskStatus.COMPLETED and step.retries == 0
    rec = journal.read(store.journal_path(lid))[-1]
    assert rec["control"] == "done" and rec["summary"] == "stop here"


# --- 4b) retries exhausted -> FAILED --------------------------------------------

def test_gate_exhausted_failed(tmp_path):
    h = StubHarness({("plan", 1, None): ['no json at all']})  # always malformed
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.FAILED
    assert db.get_loop(lid).status == JobStatus.FAILED
    # MAX_RETRIES + 1 attempts before the gate gives up.
    assert h.calls.count(("plan", 1, None)) == LoopEngine.MAX_RETRIES + 1


# --- 4c) gate retry on a resumable harness: resume the session, ask for the line --

IMPL = ("implement", 1, None)
PLAN_LINE = '{"control":"continue","summary":"PLAN-MARKER: edit foo.py"}'


class _SessionStub(StubHarness):
    """StubHarness that records each run's prompt, session_id and resume flag."""

    def __init__(self, *a, resumable=True, **kw):
        super().__init__(*a, **kw)
        self.supports_resume = resumable
        self.runs = []

    def wrap_prompt(self, prompt):
        return "WRAPPED:" + prompt

    def run(self, prompt, **kw):
        f = kw.get("log_fields") or {}
        self.runs.append({
            "key": (f.get("step_type"), f.get("iteration"), f.get("dimension")),
            "prompt": prompt, "session_id": kw.get("session_id"), "resume": kw.get("resume"),
        })
        return super().run(prompt, **kw)

    def runs_for(self, key):
        return [r for r in self.runs if r["key"] == key]


def _impl_script(*impl_replies):
    """Plan continues once, implement replies in order, then the next plan stops."""
    return {
        ("plan", 1, None): [PLAN_LINE], IMPL: list(impl_replies),
        ("plan", 2, None): ['{"control":"done"}'],
    }


def _crashed(output="half done"):
    return HarnessResult(success=False, output=output, error="boom", exit_reason="agent_error")


def _is_full_run(run):
    return "Goal: make it better" in run["prompt"] and "PLAN-MARKER" in run["prompt"]


def test_resumable_retry_resumes_the_session_and_asks_only_for_the_control_line(tmp_path):
    h = _SessionStub(_impl_script("I edited foo.py.", '{"control":"done","changed":["foo.py"]}'))
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    first, retry = h.runs_for(IMPL)
    assert first["resume"] is False and _is_full_run(first)
    assert str(uuid.UUID(first["session_id"])) == first["session_id"]
    assert retry["resume"] is True and retry["session_id"] == first["session_id"]
    # Only the short ask, still wrapped: no goal, no plan record, no journal.
    assert retry["prompt"].startswith("WRAPPED:Your previous reply was REJECTED: ")
    assert "control line" in retry["prompt"]
    for absent in ("make it better", "PLAN-MARKER", "Current plan", "Journal so far"):
        assert absent not in retry["prompt"]
    [step] = _steps(db, lid, step_type="implement")
    assert step.status == TaskStatus.COMPLETED and step.retries == 1
    [rec] = [r for r in journal.read(store.journal_path(lid)) if r.get("step_type") == "implement"]
    assert rec["changed"] == ["foo.py"] and rec["step_id"] == step.step_id


def test_non_resumable_retry_reruns_the_whole_step_as_before(tmp_path):
    h = _SessionStub(_impl_script("I edited foo.py.", '{"control":"done"}'), resumable=False)
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    first, retry = h.runs_for(IMPL)
    assert _is_full_run(first) and _is_full_run(retry)
    assert "REJECTED" in retry["prompt"]
    assert all(r["session_id"] is None and r["resume"] is False for r in h.runs)
    [step] = _steps(db, lid, step_type="implement")
    assert step.status == TaskStatus.COMPLETED and step.retries == 1


def test_failed_first_attempt_is_rerun_whole_in_a_new_session(tmp_path):
    h = _SessionStub(_impl_script(_crashed(), '{"control":"done"}'))
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    first, retry = h.runs_for(IMPL)
    assert retry["resume"] is False and _is_full_run(retry)
    assert retry["session_id"] not in (None, first["session_id"])


def test_failed_resume_is_followed_by_a_full_rerun_in_a_new_session(tmp_path):
    h = _SessionStub(_impl_script("I edited foo.py.", _crashed(), '{"control":"done"}'))
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    first, resumed, rerun = h.runs_for(IMPL)
    assert resumed["resume"] is True and resumed["session_id"] == first["session_id"]
    assert rerun["resume"] is False and _is_full_run(rerun)
    assert rerun["session_id"] not in (None, first["session_id"])
    [step] = _steps(db, lid, step_type="implement")
    assert step.retries == 2


def test_each_full_run_gets_a_new_session_and_a_resume_targets_the_latest(tmp_path):
    h = _SessionStub(_impl_script("no line", _crashed(), "no line again", '{"control":"done"}'))
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    runs = h.runs_for(IMPL)
    assert [r["resume"] for r in runs] == [False, True, False, True]
    a, b = runs[0]["session_id"], runs[2]["session_id"]
    assert a != b
    assert [r["session_id"] for r in runs] == [a, a, b, b]
    # Every full run across the loop got its own session.
    full_ids = [r["session_id"] for r in h.runs if not r["resume"]]
    assert len(set(full_ids)) == len(full_ids)


def test_resumed_retries_keep_max_retries(tmp_path):
    h = _SessionStub({("plan", 1, None): ["no json at all"]})
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.FAILED
    runs = h.runs_for(("plan", 1, None))
    assert len(runs) == LoopEngine.MAX_RETRIES + 1
    assert [r["resume"] for r in runs] == [False] + [True] * LoopEngine.MAX_RETRIES
    assert len({r["session_id"] for r in runs}) == 1


def test_usage_limit_on_a_resumed_retry_stops_the_loop(tmp_path):
    h = _SessionStub(_impl_script("I edited foo.py.", _limit()))
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.STOPPED
    loop = db.get_loop(lid)
    assert loop.status == JobStatus.STOPPED and loop.stop_reason == REASON
    first, resumed = h.runs_for(IMPL)
    assert resumed["resume"] is True
    assert h.runs[-1] is resumed  # no further retry, no later step
    [step] = _steps(db, lid, step_type="implement")
    assert step.status == TaskStatus.STOPPED and step.retries == 1


def _stream_proc(text, model_usage=None):
    """A fake `claude -p` process: it reads its stdin prompt (kept in .prompt), then
    emits `text`, and a result event carrying model_usage when given."""
    read = threading.Event()
    proc = MagicMock()

    def write(prompt):
        proc.prompt = prompt
        read.set()

    def stdout():
        read.wait(5)
        yield json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
        if model_usage is not None:
            yield json.dumps({"type": "result", "result": text, "modelUsage": model_usage,
                              "usage": {"input_tokens": 9999, "output_tokens": 9999}})

    proc.stdin.write.side_effect = write
    proc.stdout = stdout()
    proc.stderr.read.return_value = ""
    proc.returncode = 0
    return proc


def test_claude_code_gate_retry_resumes_the_session(tmp_path):
    db, store, lid = _build(tmp_path)
    procs = [_stream_proc("Planned it."), _stream_proc('{"control":"done"}')]
    with patch("minimise.agents.harness.subprocess.Popen", side_effect=procs) as popen:
        control = LoopEngine(factory=_fixed(ClaudeCodeHarness()), store=store, db=db)._run_step(
            lid, store.load_spec(lid), store.journal_path(lid), "plan", Worker(prompt="plan it"), 1
        )

    assert control == "done"
    first, resumed = (c.args[0] for c in popen.call_args_list)
    sid = first[first.index("--session-id") + 1]
    assert resumed[resumed.index("--resume") + 1] == sid and "--session-id" not in resumed
    assert resumed[resumed.index("--system-prompt") + 1] == first[first.index("--system-prompt") + 1]
    assert "Goal: make it better" in procs[0].prompt
    assert "Goal:" not in procs[1].prompt
    assert "ONLY the single JSON control line" in procs[1].prompt


def test_claude_code_step_stores_model_usage_summed_over_models_and_attempts(tmp_path):
    db, store, lid = _build(tmp_path)
    opus = {"inputTokens": 2, "outputTokens": 4, "cacheReadInputTokens": 11334,
            "cacheCreationInputTokens": 18110, "costUSD": 0.0037, "contextWindow": 1000000}
    haiku = {"inputTokens": 300, "outputTokens": 50, "cacheReadInputTokens": 1000,
             "cacheCreationInputTokens": 0, "costUSD": 0.0009}
    procs = [
        _stream_proc("Planned it.", {"claude-opus-5-5": opus, "claude-haiku-5-5": haiku}),
        _stream_proc('{"control":"done"}', {"claude-opus-5-5": opus}),  # the gate retry
    ]
    with patch("minimise.agents.harness.subprocess.Popen", side_effect=procs):
        LoopEngine(factory=_fixed(ClaudeCodeHarness()), store=store, db=db)._run_step(
            lid, store.load_spec(lid), store.journal_path(lid), "plan", Worker(prompt="plan it"), 1
        )

    [step] = db.list_loop_steps(lid)
    assert step.retries == 1
    assert step.tokens == TokenUsage(
        input=304, cache_read=23668, cache_write=36220, output=58, reasoning=None,
        by_model={
            "claude-opus-5-5": {"input": 4, "cache_read": 22668, "cache_write": 36220, "output": 8},
            "claude-haiku-5-5": {"input": 300, "cache_read": 1000, "cache_write": 0, "output": 50},
        },
    )


# --- 5) resume re-runs ONLY the missing dimension -------------------------------

def test_resume_reruns_only_missing_dimension(tmp_path):
    db, store, lid = _build(tmp_path)
    jpath = store.journal_path(lid)
    # Iteration 1 fully committed; iteration 2 partially done: plan + implement +
    # dimension "a" landed, dimension "b" is missing.
    journal.write_commit_marker(jpath, 1)
    for rec in (
        {"control": "continue", "iteration": 2, "step_type": "plan", "dimension": None},
        {"control": "done", "iteration": 2, "step_type": "implement", "dimension": None},
        {"control": "done", "findings": "f", "iteration": 2, "step_type": "evaluate", "dimension": "a"},
    ):
        journal.append(jpath, rec)

    h = StubHarness({
        ("evaluate", 2, "b"): ['{"control":"done","findings":"f"}'],
        ("plan", 3, None): ['{"control":"done"}'],  # iter 3 planner stops -> COMPLETED
    })
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED
    # Only the missing dimension re-ran; plan/implement/dim-a were NOT re-invoked.
    assert ("evaluate", 2, "b") in h.calls
    assert ("evaluate", 2, "a") not in h.calls
    assert ("plan", 2, None) not in h.calls
    assert ("implement", 2, None) not in h.calls
    assert journal.last_committed_iteration(jpath) == 2


def test_run_stops_steps_a_dead_engine_left_running_then_resumes(tmp_path):
    """An engine died mid-iteration 1 (loop left FAILED): plan and implement had
    finished, evaluator "a" was RUNNING. The next run marks that row STOPPED,
    re-runs only what the journal lacks, and leaves no RUNNING step."""
    from datetime import datetime
    from minimise.models import LoopStep

    db, store, lid = _build(tmp_path)
    jpath = store.journal_path(lid)
    for rec in (
        {"control": "continue", "iteration": 1, "step_type": "plan", "dimension": None},
        {"control": "done", "iteration": 1, "step_type": "implement", "dimension": None},
    ):
        journal.append(jpath, rec)
    for step_id, step_type, dim, status in (
        ("step-plan", "plan", None, TaskStatus.COMPLETED),
        ("step-impl", "implement", None, TaskStatus.COMPLETED),
        ("step-orphan", "evaluate", "a", TaskStatus.RUNNING),
    ):
        db.create_loop_step(LoopStep(
            step_id=step_id, loop_id=lid, iteration=1, step_type=step_type,
            dimension=dim, status=status, started_at=datetime.utcnow(),
            completed_at=datetime.utcnow() if status == TaskStatus.COMPLETED else None,
        ))
    db.update_loop_status(lid, status=JobStatus.FAILED)

    h = StubHarness({("plan", 2, None): ['{"control":"done"}']})
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)

    assert status == JobStatus.COMPLETED
    assert journal.last_committed_iteration(jpath) == 1
    # Finished steps aren't re-run; the orphaned evaluator (no journal record) is.
    assert ("plan", 1, None) not in h.calls and ("implement", 1, None) not in h.calls
    assert h.calls.count(("evaluate", 1, "a")) == 1 and ("evaluate", 1, "b") in h.calls
    steps = db.list_loop_steps(lid)
    orphan = next(s for s in steps if s.step_id == "step-orphan")
    assert orphan.status == TaskStatus.STOPPED and orphan.completed_at is not None
    assert not [s for s in steps if s.status == TaskStatus.RUNNING]


# --- 6) external stop mid-run -> halts after in-flight step, leaves STOPPED ------

def test_external_stop_halts_and_stays_stopped(tmp_path):
    db, store, lid = _build(tmp_path)

    def flip(key):
        # Flip to STOPPED right after the iter-1 plan step runs; the engine polls
        # loops.status before each subsequent step and should halt.
        if key == ("plan", 1, None):
            db.update_loop_status(lid, status=JobStatus.STOPPED)

    h = StubHarness({("plan", 1, None): ['{"control":"continue"}']}, on_call=flip)
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.STOPPED
    assert db.get_loop(lid).status == JobStatus.STOPPED
    # Halted before implement — the in-flight plan step was the last agent call.
    assert ("implement", 1, None) not in h.calls
    assert journal.last_committed_iteration(store.journal_path(lid)) == 0


# --- 6b) usage limit -> no retry, step + loop STOPPED with the reason, resumable --

RESET = "resets 4:40am (America/Los_Angeles)"
REASON = f"usage limit: {RESET}"


def _limit(reset=RESET):
    """What the Claude harness returns when the subscription limit ends the run."""
    return HarnessResult(success=False, output=f"You've hit your session limit · {reset}",
                         exit_reason="agent_error", usage_limit=reset)


def _steps(db, lid, **match):
    return [st for st in db.list_loop_steps(lid)
            if all(getattr(st, k) == v for k, v in match.items())]


@pytest.mark.parametrize("key", [("plan", 1, None), ("implement", 1, None)])
def test_usage_limit_stops_the_loop_without_retrying(tmp_path, key):
    h = StubHarness({("plan", 1, None): ['{"control":"continue"}'], key: [_limit()]})
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.STOPPED
    loop = db.get_loop(lid)
    assert loop.status == JobStatus.STOPPED and loop.stop_reason == REASON
    assert h.calls.count(key) == 1  # no gate retry
    assert h.calls[-1] == key  # no later step ran
    [step] = _steps(db, lid, step_type=key[0])
    assert step.status == TaskStatus.STOPPED and step.retries == 0
    assert step.completed_at is not None
    # Nothing journaled for the limited step, so resume re-runs it.
    assert not [r for r in journal.read(store.journal_path(lid)) if r.get("step_type") == key[0]]


def test_usage_limit_without_reset_text_is_just_usage_limit(tmp_path):
    h = StubHarness({("plan", 1, None): [_limit(reset="")]})
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.STOPPED
    assert db.get_loop(lid).stop_reason == "usage limit"


# --- 6c) tokens: a step stores every attempt's, gate retries included ---------

def _tokens(n, model="claude-opus-5-5"):
    """TokenUsage of n input, 10n cache_read, 2n cache_write, n output, all on one model."""
    counts = {"input": n, "cache_read": 10 * n, "cache_write": 2 * n, "output": n}
    return TokenUsage(**counts, by_model={model: dict(counts)})


def test_gate_retry_tokens_add_up_on_the_step(tmp_path):
    h = StubHarness({("plan", 1, None): [
        HarnessResult(success=True, output="no json at all", tokens=_tokens(1)),
        HarnessResult(success=True, output='{"control":"done"}', tokens=_tokens(5, "claude-haiku-5-5")),
    ]})
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    [step] = _steps(db, lid, step_type="plan")
    assert step.retries == 1
    assert step.tokens == TokenUsage(input=6, cache_read=60, cache_write=12, output=6, by_model={
        "claude-opus-5-5": {"input": 1, "cache_read": 10, "cache_write": 2, "output": 1},
        "claude-haiku-5-5": {"input": 5, "cache_read": 50, "cache_write": 10, "output": 5},
    })


def test_a_harness_reporting_no_tokens_leaves_every_step_null(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue"}'],
        ("plan", 2, None): ['{"control":"done"}'],
    })
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    steps = db.list_loop_steps(lid)
    assert {s.step_type for s in steps} == {"plan", "implement", "evaluate"}
    assert [s.tokens for s in steps] == [None] * len(steps)


def test_a_usage_limit_stop_keeps_the_tokens_already_used(tmp_path):
    """The limited attempt's tokens add to the rejected attempt's before it."""
    h = StubHarness({("plan", 1, None): [
        HarnessResult(success=True, output="no json at all", tokens=_tokens(1)),
        dataclasses.replace(_limit(), tokens=_tokens(2)),
    ]})
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.STOPPED
    [step] = _steps(db, lid, step_type="plan")
    assert step.status == TaskStatus.STOPPED
    assert step.tokens == _tokens(3)


# --- 6d) agent_runs: one row per agent call, a gate retry is its own run ------

def _runs(db, lid):
    return [(r.step_id, r.ended_at is not None, r.tokens) for r in db.list_agent_runs_for_loop(lid)]


def test_a_gate_retry_writes_two_runs_for_its_step(tmp_path):
    h = StubHarness({("plan", 1, None): [
        HarnessResult(success=True, output="no json at all", tokens=_tokens(1)),
        HarnessResult(success=True, output='{"control":"done"}', tokens=_tokens(5)),
    ]})
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    [step] = _steps(db, lid, step_type="plan")
    assert _runs(db, lid) == [(step.step_id, True, _tokens(1)), (step.step_id, True, _tokens(5))]
    assert all(r.job_id is None and r.task_id is None and r.attempt is None
               for r in db.list_agent_runs_for_loop(lid))


def test_every_dispatched_step_writes_one_run(tmp_path):
    h = StubHarness({("plan", 1, None): ['{"control":"continue"}'],
                     ("plan", 2, None): ['{"control":"done"}']})
    status, db, store, lid = _run(tmp_path, h)

    assert status == JobStatus.COMPLETED
    assert sorted(r[0] for r in _runs(db, lid)) == sorted(s.step_id for s in db.list_loop_steps(lid))
    assert [r[1:] for r in _runs(db, lid)] == [(True, None)] * len(h.calls)  # none reported tokens


def test_a_run_whose_harness_raised_is_finished_without_tokens(tmp_path):
    h = StubHarness({}, on_call=_crash_on(("plan", 1, None)))
    db, store, lid = _build(tmp_path)

    with pytest.raises(RuntimeError, match="interrupted"):
        LoopEngine(factory=_fixed(h), store=store, db=db)._run_step(
            lid, store.load_spec(lid), store.journal_path(lid), "plan", Worker(prompt="plan it"), 1)

    [step] = db.list_loop_steps(lid)
    assert _runs(db, lid) == [(step.step_id, True, None)]


def test_a_carried_over_pass_writes_no_run(tmp_path):
    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail",
                             (2, "b"): "fail"}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, _select_spec(dims=("a", "b")))

    assert status == JobStatus.COMPLETED
    (carried,) = [r for r in _eval_recs(store, lid, 2) if r["dimension"] == "a"]
    assert "evaluated_in_iteration" in carried  # a was carried over, not re-run
    run_steps = [r[0] for r in _runs(db, lid)]
    assert carried["step_id"] not in run_steps
    assert len(run_steps) == len(h.calls)  # one run per agent call, none for the carry


def _limited_eval_wave(tmp_path):
    """Iteration 1, max_concurrent 1: a passes, b hits the limit, c is queued behind it."""
    spec = _select_spec(dims=("a", "b", "c"), select=None, max_concurrent=1)
    db, store, lid = _build(tmp_path, spec)
    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue"}'],
        ("evaluate", 1, "a"): [_verdict_line("pass")],
        ("evaluate", 1, "b"): [_limit()],
    })
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    return status, h, db, store, lid


def test_usage_limit_in_evaluate_wave_dispatches_no_later_dimension(tmp_path):
    status, h, db, store, lid = _limited_eval_wave(tmp_path)

    assert status == JobStatus.STOPPED
    assert db.get_loop(lid).stop_reason == REASON
    assert _eval_calls(h, 1) == [("evaluate", 1, "a"), ("evaluate", 1, "b")]
    assert not _steps(db, lid, dimension="c")  # never started: no step row either
    [b] = _steps(db, lid, dimension="b")
    assert b.status == TaskStatus.STOPPED and b.retries == 0
    assert not _steps(db, lid, status=TaskStatus.RUNNING)
    # The stopped iteration is not closed, or resume would skip b and c.
    assert journal.last_committed_iteration(store.journal_path(lid)) == 0


def test_resume_after_usage_limit_reruns_only_the_stopped_and_undispatched_dimensions(tmp_path):
    _, _, db, store, lid = _limited_eval_wave(tmp_path)

    h = StubHarness({("plan", 2, None): ['{"control":"done"}']})
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)

    assert status == JobStatus.COMPLETED
    assert db.get_loop(lid).stop_reason is None  # starting again cleared it
    # b (stopped) and c (never dispatched) re-ran; a, plan and implement did not.
    assert _eval_calls(h, 1) == [("evaluate", 1, "b"), ("evaluate", 1, "c")]
    assert ("plan", 1, None) not in h.calls and ("implement", 1, None) not in h.calls
    assert journal.last_committed_iteration(store.journal_path(lid)) == 1
    assert _one_valid_record_per_dimension(store, lid, 1, "abc")


def test_start_clears_stop_reason_before_running(tmp_path):
    db, store, lid = _build(tmp_path)
    db.update_loop_status(lid, status=JobStatus.STOPPED, stop_reason=REASON)
    seen = []
    h = StubHarness({}, on_call=lambda key: seen.append(db.get_loop(lid).stop_reason))
    LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert seen and seen[0] is None


def test_external_stop_during_evaluate_wave_leaves_the_iteration_open(tmp_path):
    db, store, lid = _build(tmp_path)  # select: all, both dimensions run at once

    def flip(key):
        if key == ("evaluate", 1, "a"):
            db.update_loop_status(lid, status=JobStatus.STOPPED)

    h = StubHarness({("plan", 1, None): ['{"control":"continue"}']}, on_call=flip)
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.STOPPED
    assert journal.last_committed_iteration(store.journal_path(lid)) == 0
    assert db.get_loop(lid).stop_reason is None  # a manual stop gives no reason


def test_stop_during_a_step_spends_no_retry(tmp_path):
    db, store, lid = _build(tmp_path)

    def flip(key):
        db.update_loop_status(lid, status=JobStatus.STOPPED)

    h = StubHarness({("plan", 1, None): ["not json", '{"control":"done"}']}, on_call=flip)
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.STOPPED
    assert h.calls == [("plan", 1, None)]
    [step] = db.list_loop_steps(lid)
    assert step.status == TaskStatus.STOPPED and step.completed_at is not None


# --- 7) engine stamps metadata onto every non-marker journal line ---------------

def test_journal_lines_carry_engine_metadata(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue","plan":"go"}'],
        ("implement", 1, None): ['{"control":"done"}'],
        ("evaluate", 1, "a"): ['{"control":"done","findings":"f"}'],
        ("evaluate", 1, "b"): ['{"control":"done","findings":"f"}'],
        ("plan", 2, None): ['{"control":"done"}'],
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    assert journal.last_committed_iteration(store.journal_path(lid)) == 1

    recs = [r for r in journal.read(store.journal_path(lid)) if "marker" not in r]
    assert recs  # at least one committed step landed
    for r in recs:
        for key in ("timestamp", "loop_id", "step_id", "iteration", "step_type"):
            assert key in r, f"missing {key} in {r}"
        assert r["loop_id"] == lid
        assert r["step_type"] in {"plan", "implement", "evaluate"}


# --- 8) mid-run patch adds a dimension + bumps plan_version -> next iteration ---

def test_mid_run_patch_adds_dimension_and_stamps_plan_version(tmp_path):
    db, store, lid = _build(tmp_path)
    patched = {"done": False}

    def add_dimension_and_bump(key):
        # Fires once, mid iteration-1 evaluate fan-out: patches in dimension "c"
        # and bumps plan_version, as if a concurrent `mini loop patch` landed.
        if key == ("evaluate", 1, "a") and not patched["done"]:
            patched["done"] = True
            current = store.load_spec(lid)
            data = current.model_dump()
            data["loop"]["evaluate"]["dimensions"].append({"name": "c", "rubric": "rc"})
            data["plan_version"] = current.plan_version + 1
            store.patch(lid, LoopSpec.model_validate(data))

    h = StubHarness({
        ("plan", 1, None): ['{"control":"continue","plan":"go"}'],
        ("implement", 1, None): ['{"control":"done"}'],
        ("evaluate", 1, "a"): ['{"control":"done","findings":"f"}'],
        ("evaluate", 1, "b"): ['{"control":"done","findings":"f"}'],
        ("plan", 2, None): ['{"control":"continue"}'],
        ("implement", 2, None): ['{"control":"done"}'],
        ("evaluate", 2, "a"): ['{"control":"done","findings":"f"}'],
        ("evaluate", 2, "b"): ['{"control":"done","findings":"f"}'],
        ("evaluate", 2, "c"): ['{"control":"done","findings":"f"}'],
        ("plan", 3, None): ['{"control":"done"}'],
    }, on_call=add_dimension_and_bump)

    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED

    # The added dimension ran on the very next iteration.
    assert ("evaluate", 2, "c") in h.calls

    recs = [r for r in journal.read(store.journal_path(lid)) if "marker" not in r]
    iter1 = [r for r in recs if r.get("iteration") == 1]
    iter2 = [r for r in recs if r.get("iteration") == 2]
    assert iter1 and all(r.get("plan_version") == 1 for r in iter1)
    assert iter2 and all(r.get("plan_version") == 2 for r in iter2)


# --- 9) evaluate.select: failed — carried-over passes and the re-check ----------

def _select_spec(dims=("a", "b", "c"), select="failed", max_iterations=4, max_concurrent=2):
    evaluate = {"max_concurrent": max_concurrent,
                "dimensions": [{"name": n, "rubric": f"r{n}"} for n in dims]}
    if select is not None:
        evaluate["select"] = select
    return {**SPEC, "max_iterations": max_iterations,
            "loop": {**SPEC["loop"], "evaluate": evaluate}}


def _verdict_line(verdict):
    return json.dumps({"control": "done", "verdict": verdict, "findings": "f"})


def _script(evals, stop_at):
    """Planner continues every iteration before `stop_at`, then stops there.
    `evals` maps (iteration, dimension) -> a verdict string or a list of raw replies."""
    s = {("plan", i, None): ['{"control":"continue"}'] for i in range(1, stop_at)}
    s[("plan", stop_at, None)] = ['{"control":"done"}']
    for (i, d), out in evals.items():
        s[("evaluate", i, d)] = out if isinstance(out, list) else [_verdict_line(out)]
    return s


def _eval_recs(store, lid, iteration):
    return [r for r in journal.read(store.journal_path(lid))
            if r.get("iteration") == iteration and r.get("step_type") == "evaluate"]


def _valid(rec):
    try:
        journal.parse_control(rec)
        return True
    except ValueError:
        return False


def _one_valid_record_per_dimension(store, lid, iteration, dims):
    names = sorted(r["dimension"] for r in _eval_recs(store, lid, iteration) if _valid(r))
    return names == sorted(dims)


def _eval_calls(h, iteration):
    return [k for k in h.calls if k[0] == "evaluate" and k[1] == iteration]


def _crash_on(key, nth=1):
    """on_call hook that raises the nth time `key` is dispatched (simulated crash)."""
    seen = {"n": 0}

    def hook(k):
        if k == key:
            seen["n"] += 1
            if seen["n"] == nth:
                raise RuntimeError("interrupted")
    return hook


class _PromptStub(StubHarness):
    """StubHarness that also keeps the last prompt sent for each key."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.prompts = {}

    def run(self, prompt, **kw):
        f = kw.get("log_fields") or {}
        self.prompts[(f.get("step_type"), f.get("iteration"), f.get("dimension"))] = prompt
        return super().run(prompt, **kw)


def test_prompt_carries_non_ascii_journal_text_raw(tmp_path):
    h = _PromptStub({
        ("plan", 1, None): ['{"control":"continue","plan":"Глава первая"}'],
        ("plan", 2, None): ['{"control":"done"}'],
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    for key in [("implement", 1, None), ("plan", 2, None)]:
        assert "Глава первая" in h.prompts[key]
        assert "\\u" not in h.prompts[key]


# --- each step gets only the journal it needs -----------------------------------

def _two_iteration_prompts(tmp_path):
    """Iteration 1 plans, implements and evaluates; iteration 2 re-plans (its
    first reply is rejected), implements again and the planner stops in 3."""
    h = _PromptStub({
        ("plan", 1, None): ['{"control":"continue","summary":"PLAN-ONE"}'],
        ("implement", 1, None): ['{"control":"done","summary":"IMPL-ONE"}'],
        ("evaluate", 1, "a"): ['{"control":"done","verdict":"fail","findings":"FIND-A1"}'],
        ("evaluate", 1, "b"): ['{"control":"done","verdict":"pass","findings":"FIND-B1"}'],
        ("plan", 2, None): ['{"control":"bogus","summary":"PLAN-REJECTED"}',
                            '{"control":"continue","summary":"PLAN-TWO"}'],
        ("implement", 2, None): ['{"control":"done","summary":"IMPL-TWO"}'],
        ("evaluate", 2, "a"): ['{"control":"done","verdict":"pass","findings":"FIND-A2"}'],
        ("evaluate", 2, "b"): ['{"control":"done","verdict":"pass","findings":"FIND-B2"}'],
        ("plan", 3, None): ['{"control":"done"}'],
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    return h


def test_evaluator_prompt_holds_goal_and_rubric_and_no_journal(tmp_path):
    h = _two_iteration_prompts(tmp_path)
    for dim, rubric, other in (("a", "ra", "FIND-B1"), ("b", "rb", "FIND-A1")):
        prompt = h.prompts[("evaluate", 2, dim)]
        assert "Goal: make it better" in prompt
        assert f"Rubric: {rubric}" in prompt
        assert "Journal so far:" not in prompt
        for absent in ("step_id", "PLAN-ONE", "PLAN-TWO", "IMPL-TWO", other):
            assert absent not in prompt


def test_implementer_prompt_holds_only_newest_valid_plan_record(tmp_path):
    h = _two_iteration_prompts(tmp_path)
    prompt = h.prompts[("implement", 2, None)]
    assert "Goal: make it better" in prompt
    assert "Current plan:" in prompt and "Journal so far:" not in prompt
    assert "PLAN-TWO" in prompt
    for absent in ("PLAN-ONE", "PLAN-REJECTED", "IMPL-ONE", "FIND-A1", "FIND-B1"):
        assert absent not in prompt


def test_planner_prompt_holds_the_whole_journal(tmp_path):
    h = _two_iteration_prompts(tmp_path)
    prompt = h.prompts[("plan", 2, None)]
    assert "Journal so far:" in prompt
    for present in ("PLAN-ONE", "IMPL-ONE", "FIND-A1", "FIND-B1"):
        assert present in prompt


def test_latest_valid_plan_skips_a_newer_invalid_plan_record():
    recs = [
        {"control": "continue", "summary": "old", "step_type": "plan", "iteration": 1},
        {"control": "done", "step_type": "implement", "iteration": 1},
        {"marker": "commit", "iteration": 1},
        {"control": "continue", "summary": "valid", "step_type": "plan", "iteration": 2},
        {"control": "done", "verdict": "pass", "step_type": "evaluate", "iteration": 2},
        {"control": "bogus", "summary": "invalid", "step_type": "plan", "iteration": 2},
        {"control": "failed", "summary": "no handover", "step_type": "plan", "iteration": 2},
    ]
    assert _latest_valid_plan(recs)["summary"] == "valid"
    assert _latest_valid_plan([r for r in recs if r.get("step_type") != "plan"]) is None


def test_select_omitted_dispatches_every_dimension_each_iteration(tmp_path):
    spec = _select_spec(dims=("a", "b"), select=None)
    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail",
                             (2, "a"): "pass", (2, "b"): "fail"}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, spec)
    assert status == JobStatus.COMPLETED
    assert store.load_spec(lid).loop.evaluate.select == "all"
    for i in (1, 2):
        assert sorted(_eval_calls(h, i)) == [("evaluate", i, "a"), ("evaluate", i, "b")]
    recs = [r for r in journal.read(store.journal_path(lid)) if "marker" not in r]
    assert not any("evaluated_in_iteration" in r for r in recs)
    assert not any("recheck" in r for r in recs)


def test_select_failed_carries_pass_and_reruns_fail_and_blocked(tmp_path):
    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail", (1, "c"): "blocked",
                             (2, "b"): "fail", (2, "c"): "pass"}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, _select_spec())
    assert status == JobStatus.COMPLETED
    assert sorted(_eval_calls(h, 2)) == [("evaluate", 2, "b"), ("evaluate", 2, "c")]

    (rec,) = [r for r in _eval_recs(store, lid, 2) if r["dimension"] == "a"]
    assert rec["verdict"] == "pass"
    assert rec["control"] == "done"
    assert rec["evaluated_in_iteration"] == 1
    assert rec["findings"] == (
        "Not re-evaluated in iteration 2 (select: failed). "
        "It passed when last evaluated, in iteration 1."
    )
    assert rec["loop_id"] == lid and rec["iteration"] == 2
    assert rec["step_type"] == "evaluate" and rec["plan_version"] == 1
    assert rec["timestamp"] and rec["step_id"]
    assert "recheck" not in rec

    (step,) = [s for s in db.list_loop_steps(lid) if s.step_id == rec["step_id"]]
    assert step.step_type == "evaluate" and step.dimension == "a" and step.iteration == 2
    assert step.status == TaskStatus.COMPLETED
    assert step.started_at is not None and step.started_at == step.completed_at
    assert _one_valid_record_per_dimension(store, lid, 2, "abc")


def test_select_failed_carry_copies_evaluated_in_iteration(tmp_path):
    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail",
                             (2, "b"): "fail", (3, "b"): "fail"}, stop_at=4))
    status, db, store, lid = _run(tmp_path, h, _select_spec(dims=("a", "b")))
    assert status == JobStatus.COMPLETED
    assert ("evaluate", 2, "a") not in h.calls and ("evaluate", 3, "a") not in h.calls
    for i in (2, 3):
        (rec,) = [r for r in _eval_recs(store, lid, i) if r["dimension"] == "a"]
        assert rec["evaluated_in_iteration"] == 1
        assert rec["findings"].endswith("It passed when last evaluated, in iteration 1.")


def test_select_failed_rechecks_carried_when_all_dispatched_pass(tmp_path):
    h = _PromptStub(_script({(1, "a"): "pass", (1, "b"): "fail",
                             (2, "a"): "pass", (2, "b"): "pass"}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, _select_spec(dims=("a", "b")))
    assert status == JobStatus.COMPLETED
    # a is re-checked in iteration 2, after the first wave (b).
    calls = _eval_calls(h, 2)
    assert calls == [("evaluate", 2, "b"), ("evaluate", 2, "a")]

    recs = {r["dimension"]: r for r in _eval_recs(store, lid, 2)}
    assert recs["a"]["recheck"] is True
    assert "recheck" not in recs["b"]
    assert not any("evaluated_in_iteration" in r for r in _eval_recs(store, lid, 2))
    assert not any("recheck" in r for r in _eval_recs(store, lid, 1))
    # The re-check shares wave 1's snapshot: blind to b's iteration-2 verdict.
    assert recs["b"]["step_id"] not in h.prompts[("evaluate", 2, "a")]
    assert _one_valid_record_per_dimension(store, lid, 2, "ab")


def test_select_failed_stop_during_first_wave_skips_the_recheck(tmp_path):
    db, store, lid = _build(tmp_path, _select_spec(dims=("a", "b")))

    def flip(key):
        if key == ("evaluate", 2, "b"):  # the only first-wave evaluator in iteration 2
            db.update_loop_status(lid, status=JobStatus.STOPPED)

    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail",
                             (2, "b"): "pass"}, stop_at=3), on_call=flip)
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.STOPPED
    assert db.get_loop(lid).status == JobStatus.STOPPED
    assert ("evaluate", 2, "a") not in h.calls  # no re-check after the stop
    assert journal.last_committed_iteration(store.journal_path(lid)) == 1


def test_select_failed_rechecks_everything_when_all_carried(tmp_path):
    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "pass"}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, _select_spec(dims=("a", "b")))
    assert status == JobStatus.COMPLETED
    assert sorted(_eval_calls(h, 2)) == [("evaluate", 2, "a"), ("evaluate", 2, "b")]
    assert all(r.get("recheck") is True for r in _eval_recs(store, lid, 2))
    assert not any("evaluated_in_iteration" in r for r in _eval_recs(store, lid, 2))


@pytest.mark.parametrize("reply", [
    _verdict_line("fail"),
    _verdict_line("blocked"),
    '{"control":"done","findings":"no verdict"}',
], ids=["fail", "blocked", "no-verdict"])
def test_select_failed_no_recheck_when_any_dispatched_dimension_not_pass(tmp_path, reply):
    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail", (1, "c"): "fail",
                             (2, "b"): "pass", (2, "c"): [reply]}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, _select_spec())
    assert status == JobStatus.COMPLETED
    assert sorted(_eval_calls(h, 2)) == [("evaluate", 2, "b"), ("evaluate", 2, "c")]
    (rec,) = [r for r in _eval_recs(store, lid, 2) if r["dimension"] == "a"]
    assert rec["evaluated_in_iteration"] == 1 and "recheck" not in rec


def test_select_failed_verdict_normalized_and_missing_not_carried(tmp_path):
    h = StubHarness(_script({(1, "a"): " PASS ",
                             (1, "b"): ['{"control":"done","findings":"no verdict"}'],
                             (2, "b"): "fail"}, stop_at=3))
    status, db, store, lid = _run(tmp_path, h, _select_spec(dims=("a", "b")))
    assert status == JobStatus.COMPLETED
    assert _eval_calls(h, 2) == [("evaluate", 2, "b")]
    (rec,) = [r for r in _eval_recs(store, lid, 2) if r["dimension"] == "a"]
    assert rec["evaluated_in_iteration"] == 1


def test_select_failed_patch_bump_carries_nothing(tmp_path):
    db, store, lid = _build(tmp_path, _select_spec(dims=("a", "b")))

    def bump(key):
        # A `mini loop patch` lands while iteration 2 plans: plan_version 1 -> 2.
        if key == ("plan", 2, None):
            data = store.load_spec(lid).model_dump()
            data["plan_version"] += 1
            store.patch(lid, LoopSpec.model_validate(data))

    h = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail", (2, "b"): "fail"},
                            stop_at=3), on_call=bump)
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED
    assert sorted(_eval_calls(h, 2)) == [("evaluate", 2, "a"), ("evaluate", 2, "b")]
    recs = _eval_recs(store, lid, 2)
    assert all(r["plan_version"] == 2 for r in recs)
    assert not any("evaluated_in_iteration" in r or "recheck" in r for r in recs)


def test_select_failed_resume_after_carried_records_writes_no_duplicates(tmp_path, monkeypatch):
    db, store, lid = _build(tmp_path, _select_spec(dims=("a", "b")))
    real_marker = journal.write_commit_marker

    def crash_before_commit_2(jpath, iteration):
        if iteration == 2:
            raise RuntimeError("interrupted")
        real_marker(jpath, iteration)

    monkeypatch.setattr(journal, "write_commit_marker", crash_before_commit_2)
    first = StubHarness(_script({(1, "a"): "pass", (1, "b"): "fail", (2, "b"): "fail"},
                                stop_at=3))
    with pytest.raises(RuntimeError, match="interrupted"):
        LoopEngine(factory=_fixed(first), store=store, db=db).run(lid)
    assert [r for r in _eval_recs(store, lid, 2) if "evaluated_in_iteration" in r]
    monkeypatch.setattr(journal, "write_commit_marker", real_marker)

    second = StubHarness(_script({}, stop_at=3))
    status = LoopEngine(factory=_fixed(second), store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED
    assert _eval_calls(second, 2) == []
    assert _one_valid_record_per_dimension(store, lid, 2, "ab")
    steps = [s for s in db.list_loop_steps(lid) if s.iteration == 2 and s.dimension == "a"]
    assert len(steps) == 1
    assert journal.last_committed_iteration(store.journal_path(lid)) == 2


@pytest.mark.parametrize("c_replies", [[], ['{"control":"bogus","verdict":"pass"}']],
                         ids=["no-record", "invalid-control-record"])
def test_select_failed_resume_mid_recheck_dispatches_only_missing(tmp_path, c_replies):
    db, store, lid = _build(tmp_path, _select_spec())
    evals = {(1, "a"): "pass", (1, "b"): "fail", (1, "c"): "pass", (2, "b"): "pass"}
    if c_replies:
        evals[(2, "c")] = c_replies  # the reply lands in the journal, then the retry crashes
    first = StubHarness(_script(evals, stop_at=3),
                        on_call=_crash_on(("evaluate", 2, "c"), nth=len(c_replies) + 1))
    with pytest.raises(RuntimeError, match="interrupted"):
        LoopEngine(factory=_fixed(first), store=store, db=db).run(lid)
    assert ("evaluate", 2, "a") in first.calls  # a's re-check landed before the crash
    assert len(_eval_recs(store, lid, 2)) == 2 + len(c_replies)

    second = StubHarness(_script({(2, "c"): "pass"}, stop_at=3))
    status = LoopEngine(factory=_fixed(second), store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED
    assert _eval_calls(second, 2) == [("evaluate", 2, "c")]
    recs = {r["dimension"]: r for r in _eval_recs(store, lid, 2) if _valid(r)}
    assert recs["a"]["recheck"] is True and recs["c"]["recheck"] is True
    assert "recheck" not in recs["b"]
    assert _one_valid_record_per_dimension(store, lid, 2, "abc")


def test_select_failed_never_carries_invalid_control_pass(tmp_path):
    db, store, lid = _build(tmp_path, _select_spec())
    jpath = store.journal_path(lid)
    for rec in (
        {"control": "continue", "step_type": "plan", "dimension": None},
        {"control": "done", "step_type": "implement", "dimension": None},
        # a: a valid fail, then a rejected reply claiming pass
        {"control": "done", "verdict": "fail", "step_type": "evaluate", "dimension": "a"},
        {"control": "bogus", "verdict": "pass", "step_type": "evaluate", "dimension": "a"},
        # b: only a rejected reply claiming pass
        {"control": "bogus", "verdict": "pass", "step_type": "evaluate", "dimension": "b"},
        # c: a valid pass (carried — proves carrying is live in this journal)
        {"control": "done", "verdict": "pass", "step_type": "evaluate", "dimension": "c"},
    ):
        journal.append(jpath, {**rec, "iteration": 1, "plan_version": 1})
    journal.write_commit_marker(jpath, 1)

    h = StubHarness(_script({(2, "a"): "pass", (2, "b"): "fail"}, stop_at=3))
    status = LoopEngine(factory=_fixed(h), store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED
    assert sorted(_eval_calls(h, 2)) == [("evaluate", 2, "a"), ("evaluate", 2, "b")]
    (rec,) = [r for r in _eval_recs(store, lid, 2) if r["dimension"] == "c"]
    assert rec["evaluated_in_iteration"] == 1


# --- HarnessFactory-driven per-step harness/model resolution -------------------

class RecordingHarness(AgentHarness):
    """Records the model it was constructed with and how many times run() fired."""

    def __init__(self, tag, model=None):
        self.tag = tag
        self.model = model
        self.calls = 0

    def run(self, prompt, **kw):
        self.calls += 1
        return HarnessResult(success=True, output='{"control":"done"}')


class WrappingHarness(RecordingHarness):
    """Like RecordingHarness, but exercises a real wrap_prompt override so callers
    that skip it are caught (mirrors ClaudeCodeHarness's guard-rail injection)."""

    def wrap_prompt(self, prompt):
        return "WRAPPED:" + prompt

    def run(self, prompt, **kw):
        self.last_prompt = prompt
        return super().run(prompt, **kw)


def test_run_step_wraps_prompt_via_harness(tmp_path):
    """_run_step must call harness.wrap_prompt() before run(), same as TaskExecutor."""
    built = {}

    class TrackingFactory(HarnessFactory):
        def _instantiate(self, name, model=None):
            h = built.setdefault(name, WrappingHarness(name, model))
            return h

    factory = TrackingFactory(personas={}, default_harness="claude")
    db, store, lid = _build(tmp_path)
    worker = Worker()
    eng = LoopEngine(store=store, db=db, factory=factory)

    eng._run_step(lid, store.load_spec(lid), store.journal_path(lid), "plan", worker, 1)

    assert built["claude"].last_prompt.startswith("WRAPPED:")


def test_run_step_resolves_harness_per_worker_from_factory(tmp_path):
    """_run_step asks the factory for a fresh harness per worker instead of the fixed one."""
    built = {}

    class TrackingFactory(HarnessFactory):
        def _instantiate(self, name, model=None):
            h = RecordingHarness(name, model)
            built[name] = h
            return h

    factory = TrackingFactory(personas={}, default_harness="claude")
    db, store, lid = _build(tmp_path)
    worker = Worker(harness="pi")
    eng = LoopEngine(store=store, db=db, factory=factory)

    control = eng._run_step(lid, store.load_spec(lid), store.journal_path(lid), "plan", worker, 1)

    assert control == "done"
    assert "pi" in built
    assert built["pi"].calls == 1
    assert built["pi"].model is None


def test_worker_persona_and_model_are_mutually_exclusive():
    """A persona already carries its own harness/model; combining them is ambiguous."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        Worker(persona="p", model="step-override-model")


def test_run_step_uses_persona_model(tmp_path):
    """A step assigned a persona runs with that persona's model."""
    built = {}

    class TrackingFactory(HarnessFactory):
        def _instantiate(self, name, model=None):
            h = built.setdefault(name, RecordingHarness(name, model))
            return h

    persona = Persona(name="p", system_prompt="x", model="persona-model")
    factory = TrackingFactory(personas={"p": persona}, default_harness="claude")
    db, store, lid = _build(tmp_path)
    worker = Worker(persona="p")
    eng = LoopEngine(store=store, db=db, factory=factory)

    eng._run_step(lid, store.load_spec(lid), store.journal_path(lid), "plan", worker, 1)

    assert built["claude"].model == "persona-model"
    assert built["claude"].calls == 1


def test_harness_and_model_resolution_chain_worker_beats_persona_beats_default():
    class TrackingFactory(HarnessFactory):
        def _instantiate(self, name, model=None):
            return RecordingHarness(name, model)

    persona = Persona(name="p", harness="pi", model="persona-model", system_prompt="x")
    factory = TrackingFactory(
        personas={"p": persona}, default_harness="claude", default_model="default-model"
    )

    harness = factory.for_worker(Worker(harness="pi", model="worker-model"))
    assert harness.tag == "pi" and harness.model == "worker-model"    # worker wins both

    harness = factory.for_worker(Worker(persona="p"))
    assert harness.tag == "pi" and harness.model == "persona-model"   # persona wins over default

    harness = factory.for_worker(Worker())
    assert harness.tag == "claude" and harness.model == "default-model"  # settings default


# --- mini smoke (dogfood): `loop new`/`list` against examples/example-loop.yaml -

def test_mini_loop_new_and_list_smoke(monkeypatch):
    """MINIMISE_HOME OUTSIDE the repo (/tmp) — no harness/network involved:
    `loop new` registers the shipped example spec and `loop list` shows it."""
    import minimise.interfaces.cli as cli

    spec = Path(__file__).resolve().parents[1] / "examples" / "example-loop.yaml"
    assert spec.exists(), "examples/example-loop.yaml must ship with the repo"

    with tempfile.TemporaryDirectory(dir="/tmp") as home:
        monkeypatch.setenv("MINIMISE_HOME", home)
        importlib.reload(cli)
        try:
            runner = CliRunner()
            new = runner.invoke(cli.mini, ["loop", "new", "--plan", str(spec)])
            assert new.exit_code == 0, new.output
            assert "Loop created" in new.output

            listed = runner.invoke(cli.mini, ["loop", "list"])
            assert listed.exit_code == 0, listed.output
            assert "Example Refinement Loop" in listed.output
        finally:
            monkeypatch.delenv("MINIMISE_HOME", raising=False)
            importlib.reload(cli)


def test_mini_loop_json_survives_long_names_and_brackets(monkeypatch, tmp_path):
    """--format json must not wrap at terminal width or eat [bracketed] text as markup."""
    import minimise.interfaces.cli as cli

    name = "Loop evaluate: rerun only failed dimensions " * 3 + "[bold]kept[/bold]"
    example = Path(__file__).resolve().parents[1] / "examples" / "example-loop.yaml"
    raw = yaml.safe_load(example.read_text())
    spec = tmp_path / "loop.yaml"
    spec.write_text(yaml.safe_dump({**raw, "name": name}))

    with tempfile.TemporaryDirectory(dir="/tmp") as home:
        monkeypatch.setenv("MINIMISE_HOME", home)
        importlib.reload(cli)
        try:
            runner = CliRunner()
            new = runner.invoke(cli.mini, ["loop", "new", "--plan", str(spec)])
            assert new.exit_code == 0, new.output
            loop_id = re.search(r"loop-[0-9a-f]+", new.output).group(0)

            listed = runner.invoke(cli.mini, ["loop", "list", "--format", "json"])
            assert listed.exit_code == 0, listed.output
            assert [lp["name"] for lp in json.loads(listed.output)] == [name]

            status = runner.invoke(cli.mini, ["loop", "status", loop_id, "--format", "json"])
            assert status.exit_code == 0, status.output
            assert json.loads(status.output)["name"] == name
        finally:
            monkeypatch.delenv("MINIMISE_HOME", raising=False)
            importlib.reload(cli)
