"""End-to-end LoopEngine tests driven by a deterministic STUB harness.

No LLM, no network: the StubHarness returns canned control lines keyed by
(step_type, iteration, dimension) — read off the engine's own log_fields — so
every branch of the plan→implement→evaluate orchestrator is exercised
deterministically. Mirrors the shipped `loop_engine.demo()` but as pytest,
plus resume + external-stop scenarios the demo doesn't cover.
"""

import importlib
import tempfile
from pathlib import Path

from click.testing import CliRunner

from minimise.agents.harness import AgentHarness, HarnessResult
from minimise.models import JobStatus, LoopSpec
from minimise.orchestration import loop_journal as journal
from minimise.orchestration.loop_engine import LoopEngine
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
    last repeats once exhausted, so a key hit again just re-emits it). A missing
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
        return HarnessResult(success=True, output=outs[i])


def _build(tmp_path, spec=SPEC):
    db = Database(tmp_path / "t.db")
    db.init_db()
    store = LoopStore(db, tmp_path / "jobs")
    loop = store.create(LoopSpec.model_validate(spec), "example.yaml")
    return db, store, loop.loop_id


def _run(tmp_path, harness, spec=SPEC):
    db, store, lid = _build(tmp_path, spec)
    status = LoopEngine(harness=harness, store=store, db=db).run(lid)
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


# --- 4a) malformed control -> gate re-runs the step -> succeeds within retries --

def test_malformed_control_retried_then_succeeds(tmp_path):
    h = StubHarness({
        ("plan", 1, None): ['not json at all', '{"control":"done"}'],  # retry -> valid
    })
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.COMPLETED
    assert h.calls.count(("plan", 1, None)) == 2  # one reject + one accept


# --- 4b) retries exhausted -> FAILED --------------------------------------------

def test_gate_exhausted_failed(tmp_path):
    h = StubHarness({("plan", 1, None): ['no json at all']})  # always malformed
    status, db, store, lid = _run(tmp_path, h)
    assert status == JobStatus.FAILED
    assert db.get_loop(lid).status == JobStatus.FAILED
    # MAX_RETRIES + 1 attempts before the gate gives up.
    assert h.calls.count(("plan", 1, None)) == LoopEngine.MAX_RETRIES + 1


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
    status = LoopEngine(harness=h, store=store, db=db).run(lid)
    assert status == JobStatus.COMPLETED
    # Only the missing dimension re-ran; plan/implement/dim-a were NOT re-invoked.
    assert ("evaluate", 2, "b") in h.calls
    assert ("evaluate", 2, "a") not in h.calls
    assert ("plan", 2, None) not in h.calls
    assert ("implement", 2, None) not in h.calls
    assert journal.last_committed_iteration(jpath) == 2


# --- 6) external stop mid-run -> halts after in-flight step, leaves STOPPED ------

def test_external_stop_halts_and_stays_stopped(tmp_path):
    db, store, lid = _build(tmp_path)

    def flip(key):
        # Flip to STOPPED right after the iter-1 plan step runs; the engine polls
        # loops.status before each subsequent step and should halt.
        if key == ("plan", 1, None):
            db.update_loop_status(lid, status=JobStatus.STOPPED)

    h = StubHarness({("plan", 1, None): ['{"control":"continue"}']}, on_call=flip)
    status = LoopEngine(harness=h, store=store, db=db).run(lid)
    assert status == JobStatus.STOPPED
    assert db.get_loop(lid).status == JobStatus.STOPPED
    # Halted before implement — the in-flight plan step was the last agent call.
    assert ("implement", 1, None) not in h.calls
    assert journal.last_committed_iteration(store.journal_path(lid)) == 0


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
