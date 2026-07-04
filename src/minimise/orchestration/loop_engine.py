"""LoopEngine — the deterministic plan→implement→evaluate orchestrator.

Pure code around the agent calls (the harness is injected, DI like the job
orchestrators) so the whole loop is unit-testable with a STUB harness.

Ownership: the ENGINE is the SOLE writer of journal.jsonl. Every step agent
EMITS its control line as its final output; the engine parses
``HarnessResult.output``, appends the line, then validates it. This keeps plan
and evaluate genuinely read-only (they can't write files) while still capturing
their control line, and makes the engine the single deterministic writer.

State/status/timing live in the DB (loops + loop_steps); content lives in the
journal. Terminal loops.status mapping: planner stop/done -> completed;
max_iterations ceiling -> failed; gate-exhausted/crash -> failed; external
`mini loop stop` -> stopped.
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from minimise.agents.harness import AgentHarness, ClaudeCodeHarness
from minimise.logging.backend import JsonlLogBackend
from minimise.models import JobStatus, LoopStep, TaskStatus
from minimise.orchestration import loop_journal as journal
from minimise.storage.database import Database
from minimise.storage.loop_store import LoopStore
from minimise.utils import new_id

# The three shipped defaults (used when a step's worker sets no persona/prompt).
# Opinionated toward mini-style plans, per the design; swap a worker to deviate.
DEFAULT_PROMPTS = {
    "plan": (
        "You are the PLANNER in a refinement loop. Read the goal and the journal "
        "history (prior plans, findings, and handovers). Decide the next approach "
        "for the implementer, or decide the loop is finished. Emit control 'stop' "
        "or 'done' when the goal is met or no useful progress remains; otherwise "
        "'continue' with the plan for the implementer to execute. In your control line "
        'include a "summary": one concise sentence naming the decision or the next step '
        "for the implementer."
    ),
    "implement": (
        "You are the IMPLEMENTER in a refinement loop. Execute the current plan by "
        "mutating the working tree. Emit control 'done' on success; on failure emit "
        "control 'failed' with a 'handover' explaining what blocked you so the next "
        "planner can re-plan. In your control line include a \"summary\" (one sentence "
        'on what you changed) and "changed" (a JSON array of the file paths you touched, '
        "[] if none)."
    ),
    "evaluate": (
        "You are an EVALUATOR in a refinement loop, judging ONE dimension only and "
        "blind to the other evaluators. Assess the current state of the work against "
        "your rubric and emit findings (no scores). Emit control 'done'."
    ),
}

# Appended to every step's system prompt — the shape contract (what to emit).
# The worker's prompt says WHAT to do; this says WHAT SHAPE to emit.
OUTPUT_CONTRACT = (
    "\n\n---\nOUTPUT CONTRACT: End your response with a SINGLE LINE that is one JSON "
    'object. Required key "control" — one of: continue, stop, done, failed. When '
    'control is "failed" you MUST also include a non-empty "handover" string for the '
    "next planner. Beyond control/handover, include the payload key(s) for your role "
    'so the journal stays queryable: PLANNER -> "summary" (one concise sentence on the '
    'decision/next step); IMPLEMENTER -> "summary" (what you changed, one sentence) and '
    '"changed" (a JSON array of the file paths you touched, [] if none); EVALUATOR -> '
    '"verdict" ("pass" or "fail") and "findings". Do NOT write any file for this — just '
    "emit the line as the final line of your reply."
)


class StepGateExhausted(Exception):
    """A step never emitted a valid control line within the retries cap."""


class LoopEngine:
    MAX_RETRIES = 3  # validation-gate retries, mirrors Task.retries
    MAX_REPLANS = 3  # inner implement-fail -> re-plan cycles before giving up (FAILED)

    def __init__(
        self,
        harness: Optional[AgentHarness] = None,
        store: Optional[LoopStore] = None,
        db: Optional[Database] = None,
        personas: Optional[dict] = None,
        cwd: Optional[str] = None,
    ):
        self.harness = harness or ClaudeCodeHarness()
        self.store = store
        self.db = db
        self.personas = personas or {}
        self.cwd = cwd
        self.backend = JsonlLogBackend()
        # Serialize the two shared sinks so the parallel evaluate fan-out is safe.
        self._db_lock = threading.Lock()
        self._journal_lock = threading.Lock()

    # ---- entry point --------------------------------------------------------

    def run(self, loop_id: str) -> JobStatus:
        """Run or resume a loop to a terminal status; returns that status."""
        spec = self.store.load_spec(loop_id)
        jpath = self.store.journal_path(loop_id)
        self.db.update_loop_status(
            loop_id, status=JobStatus.RUNNING, started_at=datetime.utcnow(), pid=os.getpid()
        )

        anchor = journal.last_committed_iteration(jpath)  # iterations 1..anchor done
        iteration = anchor
        status = JobStatus.FAILED
        try:
            while True:
                if self._stopped(loop_id):
                    status = JobStatus.STOPPED
                    break
                iteration += 1
                # Only the first iteration past the anchor can be partially done.
                resume = self._resume_state(jpath, iteration) if iteration == anchor + 1 else {}
                result = self._run_iteration(loop_id, spec, iteration, jpath, resume)
                if result in (JobStatus.COMPLETED, JobStatus.STOPPED, JobStatus.FAILED):
                    status = result
                    break
                if iteration >= spec.max_iterations:  # backstop — the only stop code owns
                    status = JobStatus.FAILED
                    break
        except StepGateExhausted:
            status = JobStatus.FAILED
        except Exception:
            status = JobStatus.FAILED
            self.db.update_loop_status(loop_id, status=status, completed_at=datetime.utcnow())
            raise
        self.db.update_loop_status(loop_id, status=status, completed_at=datetime.utcnow())
        return status

    # ---- one iteration ------------------------------------------------------

    def _run_iteration(self, loop_id, spec, iteration, jpath, resume) -> Optional[JobStatus]:
        """Plan -> implement (inner loop) -> evaluate fan-out -> commit marker.

        Returns COMPLETED (planner stop/done), STOPPED (external), FAILED
        (inner implement/re-plan cycle exhausted), or None (iteration
        committed — keep looping).
        """
        cfg = spec.loop
        if self._stopped(loop_id):
            return JobStatus.STOPPED

        # PLAN (reuse a finished plan line on resume, else run whole).
        control = resume.pop("plan", None) or self._run_step(
            loop_id, spec, jpath, "plan", cfg.plan, iteration
        )
        if control in ("stop", "done"):
            return JobStatus.COMPLETED

        # IMPLEMENT with the inner loop: failed -> back to PLAN, skip evaluate.
        # Bounded by MAX_REPLANS so an implement that fails while the planner
        # keeps returning 'continue' can't spin forever — exhausted => FAILED.
        # (The outer max_iterations backstop can't trip here: iteration is fixed.)
        for _ in range(self.MAX_REPLANS + 1):
            if self._stopped(loop_id):
                return JobStatus.STOPPED
            control = resume.pop("implement", None) or self._run_step(
                loop_id, spec, jpath, "implement", cfg.implement, iteration, allow_edits=True
            )
            if control != "failed":
                break
            control = self._run_step(loop_id, spec, jpath, "plan", cfg.plan, iteration)
            if control in ("stop", "done"):
                return JobStatus.COMPLETED
        else:
            return JobStatus.FAILED  # re-plan cycle exhausted, still failing

        # EVALUATE fan-out: one agent per dimension, blind, capped by max_concurrent.
        if self._stopped(loop_id):
            return JobStatus.STOPPED
        done_dims = resume.pop("eval_done", set())
        pending = [d for d in cfg.evaluate.dimensions if d.name not in done_dims]
        if pending:
            context = self._journal_text(jpath)  # one snapshot -> every dim is blind to siblings
            with ThreadPoolExecutor(max_workers=cfg.evaluate.max_concurrent) as pool:
                futures = [
                    pool.submit(
                        self._run_step, loop_id, spec, jpath, "evaluate", dim, iteration,
                        dimension=dim.name, rubric=dim.rubric, journal_context=context,
                    )
                    for dim in pending
                ]
                for f in futures:
                    f.result()  # re-raises StepGateExhausted -> engine maps to FAILED

        journal.write_commit_marker(jpath, iteration)  # journal-only, closes the iteration
        return None

    # ---- one step + validation gate -----------------------------------------

    def _run_step(self, loop_id, spec, jpath, step_type, worker, iteration,
                  dimension=None, rubric=None, allow_edits=False, journal_context=None) -> str:
        """Dispatch one step, gate its control line with bounded retries, return control."""
        system_prompt, model = self._resolve_worker(step_type, worker)
        context = journal_context if journal_context is not None else self._journal_text(jpath)

        step_id = new_id("step")
        step = LoopStep(
            step_id=step_id, loop_id=loop_id, iteration=iteration, step_type=step_type,
            dimension=dimension, status=TaskStatus.RUNNING, started_at=datetime.utcnow(),
        )
        with self._db_lock:
            self.db.create_loop_step(step)

        feedback = ""
        for attempt in range(self.MAX_RETRIES + 1):
            prompt = self._build_prompt(spec, step_type, context, rubric, feedback)
            result = self.harness.run(
                prompt, cwd=self.cwd, allow_edits=allow_edits, model=model,
                system_prompt=system_prompt + OUTPUT_CONTRACT,
                log_path=str(self.store.loop_log_path(loop_id)),
                log_fields={
                    "loop_id": loop_id, "iteration": iteration, "step_id": step_id,
                    "step_type": step_type, "dimension": dimension,
                },
                log_filter=journal.strip_control_line,  # journal owns the control line; keep logs reasoning-only
            )
            line = journal.extract_last_json(result.output)
            if line is not None:  # engine is the sole writer — stamp routing keys for resume
                stamped = {
                    **line,
                    "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                    "loop_id": loop_id,
                    "step_id": step_id,
                    "iteration": iteration,
                    "step_type": step_type,
                    "dimension": dimension,
                }
                with self._journal_lock:
                    journal.append(jpath, stamped)
            try:
                control = journal.parse_control(line) if line is not None else _no_line()
                with self._db_lock:
                    self.db.update_loop_step(
                        step_id, status=TaskStatus.COMPLETED, completed_at=datetime.utcnow()
                    )
                return control
            except ValueError as e:
                feedback = (
                    f"\n\nYour previous response was REJECTED: {e}. End your reply with a "
                    'single JSON line carrying a valid "control" (and "handover" if failed).'
                )
                if attempt < self.MAX_RETRIES:
                    with self._db_lock:
                        self.db.update_loop_step(step_id, retries=attempt + 1)

        with self._db_lock:
            self.db.update_loop_step(step_id, status=TaskStatus.FAILED, completed_at=datetime.utcnow())
        raise StepGateExhausted(f"{step_type} step {step_id} exhausted the validation gate")

    # ---- helpers ------------------------------------------------------------

    def _resolve_worker(self, step_type, worker) -> tuple[str, Optional[str]]:
        """Resolve a Worker to (system_prompt, model): persona | prompt | prompt_file | default."""
        if worker.persona:
            p = self.personas.get(worker.persona)
            if p is None:
                raise ValueError(f"unknown persona {worker.persona!r} for {step_type} step")
            return p.system_prompt, p.model
        if worker.prompt:
            return worker.prompt, None
        if worker.prompt_file:
            return Path(worker.prompt_file).read_text(), None
        return DEFAULT_PROMPTS[step_type], None

    def _build_prompt(self, spec, step_type, context, rubric, feedback) -> str:
        """The runtime turn: goal + journal history (+ dimension rubric) + retry feedback."""
        parts = [f"Goal: {spec.goal}", ""]
        parts.append("Journal so far:")
        parts.append(context or "(empty — this is the first step)")
        if step_type == "evaluate" and rubric:
            parts += ["", f"Evaluate the current work against this dimension.\nRubric: {rubric}"]
        if feedback:
            parts.append(feedback)
        return "\n".join(parts)

    def _journal_text(self, jpath) -> str:
        return "\n".join(json.dumps(r) for r in journal.read(jpath))

    def _stopped(self, loop_id) -> bool:
        loop = self.db.get_loop(loop_id)
        return loop is not None and loop.status == JobStatus.STOPPED

    def _resume_state(self, jpath, iteration) -> dict:
        """What of an interrupted iteration already finished (journal-authoritative).

        plan/implement: reuse the latest VALID control line (re-run whole otherwise).
        evaluate: the set of dimensions with a valid findings line (re-run the rest).
        """
        recs = [r for r in journal.read(jpath)
                if r.get("iteration") == iteration and "marker" not in r]
        state: dict = {}
        for kind in ("plan", "implement"):
            lines = [r for r in recs if r.get("step_type") == kind]
            if lines and (c := _safe_control(lines[-1])) is not None:
                state[kind] = c
        state["eval_done"] = {
            r.get("dimension") for r in recs
            if r.get("step_type") == "evaluate" and _safe_control(r) is not None
        }
        return state


def _no_line():
    raise ValueError("no parseable control line in step output")


def demo():
    """Self-check with a STUB harness: exercises the terminal mappings + resume."""
    import tempfile
    from minimise.agents.harness import HarnessResult
    from minimise.models import LoopSpec

    spec_dict = {
        "version": "1", "name": "Demo", "goal": "make it better", "max_iterations": 3,
        "loop": {
            "plan": {"prompt": "plan it"}, "implement": {"prompt": "do it"},
            "evaluate": {"max_concurrent": 2, "dimensions": [
                {"name": "a", "rubric": "ra"}, {"name": "b", "rubric": "rb"}]},
        },
    }

    class Stub(AgentHarness):
        """Cycles its scripts (mod len) so a per-iteration pattern repeats."""
        def __init__(self, scripts):
            self.scripts, self.i = scripts, 0
        def run(self, prompt, **kw):
            out = self.scripts[self.i % len(self.scripts)]
            self.i += 1
            return HarnessResult(success=True, output=out)

    def build(tmp):
        db = Database(Path(tmp) / "t.db"); db.init_db()
        store = LoopStore(db, Path(tmp) / "jobs")
        loop = store.create(LoopSpec.model_validate(spec_dict), "demo.yaml")
        return db, store, loop.loop_id

    # 1) plan continue -> implement done -> 2 evals -> plan done => COMPLETED
    with tempfile.TemporaryDirectory() as tmp:
        db, store, lid = build(tmp)
        eng = LoopEngine(harness=Stub([
            '{"control":"continue","plan":"go"}',      # plan (iter 1)
            '{"control":"done"}',                        # implement
            '{"control":"done","findings":"f"}',         # eval a
            '{"control":"done","findings":"f"}',         # eval b
            '{"control":"done"}',                        # plan (iter 2) -> stop
        ]), store=store, db=db)
        assert eng.run(lid) == JobStatus.COMPLETED
        assert journal.last_committed_iteration(store.journal_path(lid)) == 1

    # 2) planner never stops -> hits max_iterations ceiling => FAILED
    with tempfile.TemporaryDirectory() as tmp:
        db, store, lid = build(tmp)
        eng = LoopEngine(harness=Stub(['{"control":"continue"}', '{"control":"done"}',
                                       '{"control":"done"}', '{"control":"done"}']),
                         store=store, db=db)
        assert eng.run(lid) == JobStatus.FAILED
        assert journal.last_committed_iteration(store.journal_path(lid)) == 3

    # 3) implement failed -> inner loop back to plan, skips evaluate this pass
    with tempfile.TemporaryDirectory() as tmp:
        db, store, lid = build(tmp)
        eng = LoopEngine(harness=Stub([
            '{"control":"continue"}',                    # plan
            '{"control":"failed","handover":"stuck"}',   # implement fails
            '{"control":"done"}',                        # re-plan -> stop => COMPLETED
        ]), store=store, db=db)
        assert eng.run(lid) == JobStatus.COMPLETED
        # no commit marker: the failing pass never reached evaluate
        assert journal.last_committed_iteration(store.journal_path(lid)) == 0

    # 4) malformed control retried, then exhausted => FAILED
    with tempfile.TemporaryDirectory() as tmp:
        db, store, lid = build(tmp)
        eng = LoopEngine(harness=Stub(['no json at all']), store=store, db=db)
        assert eng.run(lid) == JobStatus.FAILED

    # 5) implement fails + planner keeps saying 'continue' -> re-plan cap => FAILED
    #    (would spin forever without MAX_REPLANS; iteration never advances here)
    class ByStep(AgentHarness):
        """Route by step: implement always fails, plan always continues."""
        def run(self, prompt, **kw):
            impl = "do it" in kw.get("system_prompt", "")
            out = ('{"control":"failed","handover":"stuck"}' if impl
                   else '{"control":"continue"}')
            return HarnessResult(success=True, output=out)

    with tempfile.TemporaryDirectory() as tmp:
        db, store, lid = build(tmp)
        eng = LoopEngine(harness=ByStep(), store=store, db=db)
        assert eng.run(lid) == JobStatus.FAILED
        # never reached evaluate/commit — the failing pass never converges
        assert journal.last_committed_iteration(store.journal_path(lid)) == 0

    print("loop_engine demo OK")


if __name__ == "__main__":
    demo()


def _safe_control(rec) -> Optional[str]:
    try:
        return journal.parse_control(rec)
    except ValueError:
        return None
