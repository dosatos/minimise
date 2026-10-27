"""Formatting helpers shared across terminal and web interfaces."""

from dataclasses import replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional, Union

from minimise.models import AgentRun, Job, JobStatus, Loop, LoopStep, TaskStatus, TokenUsage
from minimise.settings import Prices


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
# by_model or model names. Nothing about cost is stored; cost (below) appears
# only in the web API and pages, only when prices are configured. Every figure
# is a sum over agent_runs, one row per agent call; the owner is the job or
# loop the runs belong to.
# 0 when we know no tokens were used; None only when tokens were (or are being)
# used but we don't know how many, or the owner predates agent_runs.

_TOKEN_KEYS = ("input", "cache_read", "cache_write", "output", "total")


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


def _counted_runs(owner: Union[Job, Loop], runs: Iterable[AgentRun]) -> list[AgentRun]:
    """The runs a rollup counts: ended ones, and unended ones once the owner
    isn't running (killed or crashed). An unended run of a running owner is in
    progress."""
    running = owner.status == JobStatus.RUNNING
    return [run for run in runs if run.ended_at is not None or not running]


def tokens_total(owner: Union[Job, Loop], runs: Iterable[AgentRun]) -> Optional[dict]:
    """The rollup of a loop, iteration, task or job over its runs; None for an
    untracked owner. A run counts once it ended, or if it never ended and its
    owner isn't running (killed or crashed); one that hasn't ended while its
    owner runs is in progress. Each bucket and the total sum the counted runs
    that reported them: 0 with no counted runs, None when some counted but none
    reported. not_reported = counted runs with no tokens."""
    if owner.tokens_untracked:
        return None
    counted = _counted_runs(owner, runs)
    if not counted:
        return {**dict.fromkeys(_TOKEN_KEYS, 0), "not_reported": 0}
    summed = sum((run.tokens for run in counted if run.tokens is not None), None)
    return {
        **(tokens_json(summed) or dict.fromkeys(_TOKEN_KEYS)),
        "not_reported": sum(run.tokens is None for run in counted),
    }


def row_tokens(owner: Union[Job, Loop], runs: Iterable[AgentRun]) -> Optional[dict]:
    """One row's tokens (a loop step, or a job task attempt): zeros when no agent
    ran in it, None when any of its runs hasn't ended or didn't report (or the
    owner is untracked), else the sum of its runs (a resumed attempt has several)."""
    if owner.tokens_untracked:
        return None
    runs = list(runs)
    if not runs:
        return dict.fromkeys(_TOKEN_KEYS, 0)
    if any(run.ended_at is None or run.tokens is None for run in runs):
        return None
    return tokens_json(sum((run.tokens for run in runs), None))


def attempt_runs(runs: Iterable[AgentRun], task_id: Optional[str],
                 attempt: Optional[int]) -> list[AgentRun]:
    """The runs of one job task attempt. `attempt` is 0-based like
    agent_runs.attempt and Execution.attempt; a timeline Step's is 1-based, so
    pass step.attempt - 1. None (a task row with no attempt yet) has no runs."""
    if attempt is None:
        return []
    return [run for run in runs if run.task_id == task_id and run.attempt == attempt]


def loop_step_json(loop: Loop, step: LoopStep, runs: Iterable[AgentRun]) -> dict:
    """A loop step as CLI and web JSON show it; the web adds its own keys.
    `runs` are the loop's; the step's tokens come from those with its step_id."""
    return {
        "step_id": step.step_id,
        "iteration": step.iteration,
        "step_type": step.step_type,
        "dimension": step.dimension,
        "status": step.status.value,
        "tokens": row_tokens(loop, (run for run in runs if run.step_id == step.step_id)),
    }


def format_tokens(n: Optional[int]) -> str:
    """243.1M, 12.4K or the plain integer; an em dash when not reported."""
    if n is None:
        return "\u2014"
    if n >= 999_950:  # rounds to 1.0M; never "1000.0K"
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def tokens_summary(rollup: Optional[dict]) -> str:
    """A rollup on one line: "243.1M (input 120.4K · cache write 21.6M ·
    cache read 218.6M · output 2.8M)", "0" when nothing was used, an em dash for
    an untracked owner or when nothing was reported, then " (3 runs not
    reported)" when some counted runs reported nothing."""
    if rollup is None:
        return format_tokens(None)
    if rollup["total"] in (None, 0):
        text = format_tokens(rollup["total"])
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
        text += f" ({missing} run{'' if missing == 1 else 's'} not reported)"
    return text


# ---- cost: an estimate at the user's own prices, not a bill -----------------
# Computed from agent_runs tokens on every request with settings.yaml's prices
# (USD per million tokens); nothing about cost is stored, and editing the prices
# re-prices past runs. Only the web API and pages carry it, and only when prices
# are configured.

def run_cost(prices: Prices, usage: Optional[TokenUsage]) -> Optional[float]:
    """One run's cost in USD, or None when it can't be priced: it reported
    nothing, or one of its models has no price and there's no default. Runs
    that split their tokens by model (Claude's) are priced model by model; the
    rest at default. A bucket that wasn't reported adds nothing."""
    if usage is None:
        return None
    by_model = usage.by_model or {"default": {b: getattr(usage, b) for b in TokenUsage.BUCKETS}}
    usd = 0.0
    for model, counts in by_model.items():
        price = prices.models.get(model, prices.models.get("default"))
        if price is None:
            return None
        usd += sum((counts.get(b) or 0) * price[b] for b in TokenUsage.BUCKETS)
    return usd / 1_000_000


def row_cost(owner: Union[Job, Loop], runs: Iterable[AgentRun],
             prices: Prices) -> Optional[float]:
    """One row's cost_usd, next to its row_tokens: None when its tokens are
    None or any of its runs can't be priced, 0 when no agent ran in it, else
    the sum of its runs."""
    runs = list(runs)
    if row_tokens(owner, runs) is None:
        return None
    costs = [run_cost(prices, run.tokens) for run in runs]
    return None if None in costs else sum(costs)


def cost_total(owner: Union[Job, Loop], runs: Iterable[AgentRun],
               prices: Prices) -> Optional[dict]:
    """A rollup's cost_total, next to its tokens_total and over the same
    counted runs: usd sums the counted runs that can be priced, and
    lower_bound says some counted run can't be priced or reported nothing.
    usd is 0 with no counted runs, None when none of them can be priced; the
    whole rollup is None for an untracked owner."""
    if owner.tokens_untracked:
        return None
    costs = [run_cost(prices, run.tokens) for run in _counted_runs(owner, runs)]
    priced = [usd for usd in costs if usd is not None]
    return {
        "usd": sum(priced) if priced or not costs else None,
        "lower_bound": len(priced) < len(costs),
    }


def format_cost(usd: Optional[float], lower_bound: bool = False) -> str:
    """A cost as a cell shows it: "$0.94"; "<$0.01" under a cent; whole dollars
    from $100 up ("$1,234"); "$0" for exactly 0; an em dash when unknown; "≥ "
    in front of a lower bound. Halves round up, as JavaScript's toFixed does,
    so the web's formatter can print exactly the same."""
    if usd is None:
        return "\u2014"
    if usd == 0:
        text = "$0"
    elif usd < 0.01:
        text = "<$0.01"
    else:
        cents = Decimal(usd).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if cents >= 100:  # 99.996 rounds to $100.00: whole dollars, like $100
            text = f"${Decimal(usd).quantize(Decimal('1'), ROUND_HALF_UP):,}"
        else:
            text = f"${cents}"
    return f"\u2265 {text}" if lower_bound else text
