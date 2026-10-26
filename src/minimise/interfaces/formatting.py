"""Formatting helpers shared across terminal and web interfaces."""

from dataclasses import replace
from datetime import datetime
from typing import Optional

from minimise.models import JobStatus, Loop, LoopStep, TaskStatus, TokenUsage


def _now_or_default(now: Optional[datetime]) -> datetime:
    """Return now, or the current UTC time if none was supplied."""
    return now or datetime.utcnow()


def humanize_duration(total_seconds: float) -> str:
    """Format a duration in seconds using compact, human-readable units."""
    if total_seconds < 1:
        return f"{int(total_seconds * 1000)}ms"
    if total_seconds < 60:
        return f"{total_seconds:.1f}s"
    if total_seconds < 3600:
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        return f"{minutes}m {seconds}s"
    if total_seconds < 86400:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"

    days = int(total_seconds // 86400)
    hours = int((total_seconds % 86400) // 3600)
    minutes = int((total_seconds % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


def format_duration(
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
    is_running: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """Format a completed duration or the elapsed time of a running item."""
    if not started_at:
        return "\u2014"

    if is_running and not completed_at:
        elapsed = (_now_or_default(now) - started_at).total_seconds()
        return humanize_duration(elapsed)

    if not completed_at:
        return "\u2014"

    duration = (completed_at - started_at).total_seconds()
    return humanize_duration(duration)


def shown_loop_steps(loop: Loop, steps: list[LoopStep]) -> list[LoopStep]:
    """Steps as the views show them: a RUNNING step counts as running only
    while its loop is RUNNING. An engine that died mid-step left it behind, so
    it shows as STOPPED (its end stays unknown). Read-only: the rows themselves
    are repaired by `mini loop stop`, LoopStore._reconcile and LoopEngine.run."""
    if loop.status == JobStatus.RUNNING:
        return steps
    return [replace(step, status=TaskStatus.STOPPED) if step.status == TaskStatus.RUNNING else step
            for step in steps]


def evaluate_records(steps: list[LoopStep], journal_records) -> dict[str, dict]:
    """Map evaluate step_id -> its journal record; the last matching record wins.

    The engine stamps step_id on every record; older records without one
    fall back to (iteration, dimension), resolved to that slot's latest step.
    """
    evaluate = [step for step in steps if step.step_type == "evaluate"]
    step_ids = {step.step_id for step in evaluate}
    by_slot = {(step.iteration, step.dimension): step.step_id for step in evaluate}
    matched: dict[str, dict] = {}
    for record in journal_records:
        if record.get("step_type") != "evaluate":
            continue
        step_id = record.get("step_id")
        if not step_id:
            step_id = by_slot.get((record.get("iteration"), record.get("dimension")))
        if step_id in step_ids:
            matched[step_id] = record
    return matched


def carried_from(record: dict) -> Optional[int]:
    """select: failed — the iteration a carried-over pass was really evaluated in."""
    value = record.get("evaluated_in_iteration")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---- tokens: one shape for CLI --format json and the web API ----------------
# Keys are only ever added; a key never changes meaning. Never reasoning,
# by_model, model names or cost.

_TOKEN_KEYS = ("input", "cache_read", "cache_write", "output", "total")
_FINISHED_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)


def tokens_json(usage: Optional[TokenUsage]) -> Optional[dict]:
    """One run's tokens: the four buckets and their total, or None when not reported."""
    if usage is None:
        return None
    return {
        "input": usage.input,
        "cache_read": usage.cache_read,
        "cache_write": usage.cache_write,
        "output": usage.output,
        "total": usage.total,
    }


def _counted(run) -> bool:
    """A run counts in a rollup when an agent ran in it and it has finished.
    Hook executions run shell commands, never an agent, so they never count."""
    return (
        run.started_at is not None
        and run.status in _FINISHED_STATUSES
        and getattr(run, "execution_type", "task") == "task"
    )


def tokens_total(runs) -> dict:
    """The rollup of LoopStep or Execution runs: each bucket summed over the
    counted runs that reported it (None if none did), total likewise, and
    not_reported = counted runs with no tokens (the sums are then a lower bound).
    Always a dict. Loop callers drop carried-over passes first (counted_loop_steps)."""
    counted = [run for run in runs if _counted(run)]
    summed = sum((run.tokens for run in counted if run.tokens is not None), None)
    return {
        **(tokens_json(summed) or dict.fromkeys(_TOKEN_KEYS)),
        "not_reported": sum(run.tokens is None for run in counted),
    }


def counted_loop_steps(steps: list[LoopStep], journal_records) -> list[LoopStep]:
    """The steps minus passes carried over under select: failed (their journal
    record has an int evaluated_in_iteration): no agent ran in those. Pass
    shown_loop_steps output, so a dead engine's RUNNING step counts as stopped."""
    records = evaluate_records(steps, journal_records)
    return [step for step in steps if carried_from(records.get(step.step_id, {})) is None]


def loop_step_json(step: LoopStep) -> dict:
    """A loop step as CLI and web JSON show it; the web adds its own keys."""
    return {
        "step_id": step.step_id,
        "iteration": step.iteration,
        "step_type": step.step_type,
        "dimension": step.dimension,
        "status": step.status.value,
        "tokens": tokens_json(step.tokens),
    }


def format_tokens(n: Optional[int]) -> str:
    """243.1M, 12.4K or the plain integer; an em dash when not reported."""
    if n is None:
        return "\u2014"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def tokens_summary(rollup: dict, noun: str) -> str:
    """A rollup on one line: "243.1M (input 120.4K · cache write 21.6M ·
    cache read 218.6M · output 2.8M)", or an em dash when nothing was reported,
    then " (3 steps not reported)" when some counted `noun`s reported nothing."""
    if rollup["total"] is None:
        text = format_tokens(None)
    else:
        text = (
            f"{format_tokens(rollup['total'])} ("
            f"input {format_tokens(rollup['input'])} · "
            f"cache write {format_tokens(rollup['cache_write'])} · "
            f"cache read {format_tokens(rollup['cache_read'])} · "
            f"output {format_tokens(rollup['output'])})"
        )
    missing = rollup["not_reported"]
    if missing:
        text += f" ({missing} {noun}{'' if missing == 1 else 's'} not reported)"
    return text
