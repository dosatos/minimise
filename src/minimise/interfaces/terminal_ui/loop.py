"""Loop heatmap rendering — the evaluate verdict grid and stage timings."""

from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.text import Text
from minimise.interfaces.formatting import (
    counted_loop_steps,
    format_tokens,
    shown_loop_steps,
    tokens_total,
)
from minimise.models import JobStatus, TaskStatus, Loop

from ._shared import fit_width, format_duration


def _pivot_evaluate(journal_records) -> tuple:
    """Pivot evaluate journal records to (rows, iters): rows is an ordered
    {dimension: {iteration: verdict}} (dimension order = first-seen), iters is
    the sorted list of distinct iterations. verdict is agent convention, not
    schema-enforced, so we take it verbatim and let the renderer coerce it."""
    rows: dict = {}
    iters: set = set()
    for r in journal_records:
        if r.get("step_type") != "evaluate":
            continue
        dim = r.get("dimension") or "?"
        it = r.get("iteration")
        if it is None:
            continue
        iters.add(it)
        v = r.get("verdict")
        rows.setdefault(dim, {})[it] = v.strip().lower() if isinstance(v, str) else v
    return rows, sorted(iters)


def _carried_cells(journal_records) -> dict:
    """{(dimension, iteration): evaluated_in_iteration} for heatmap cells whose
    latest evaluate record is a pass carried over under select: failed (the
    engine records it without running an evaluator). Same last-record-wins
    keying as _pivot_evaluate."""
    carried: dict = {}
    for r in journal_records:
        if r.get("step_type") != "evaluate" or r.get("iteration") is None:
            continue
        key = (r.get("dimension") or "?", r.get("iteration"))
        n = r.get("evaluated_in_iteration")
        if isinstance(n, int) and not isinstance(n, bool):
            carried[key] = n
        else:
            carried.pop(key, None)
    return carried


def _recheck_cells(journal_records) -> set:
    """{(dimension, iteration)} for heatmap cells whose latest evaluate record is
    a re-check: a carried-over pass evaluated again before the loop can finish
    (select: failed). Same last-record-wins keying as _carried_cells."""
    rechecked: set = set()
    for r in journal_records:
        if r.get("step_type") != "evaluate" or r.get("iteration") is None:
            continue
        key = (r.get("dimension") or "?", r.get("iteration"))
        if r.get("recheck") is True:
            rechecked.add(key)
        else:
            rechecked.discard(key)
    return rechecked


_CARRIED_CAPTION = "faded ✓ from N = pass carried over from iteration N, not re-run (select: failed)"
_RECHECK_CAPTION = "re-check = carried-over pass evaluated again before the loop can finish (select: failed)"


def _now_iteration(loop: Loop, steps: list) -> Optional[int]:
    """The iteration whose column reads "N now": the current one (the highest
    over the loop's steps, as the "Iteration:" line shows) while the loop runs.
    None otherwise, so a finished, failed, stopped or pending loop shows numbers only."""
    if loop.status != JobStatus.RUNNING or not steps:
        return None
    return max(s.iteration for s in steps)


def _iteration_header(it, now_it) -> str:
    return f"{it} now" if it == now_it else str(it)


def _verdict_cell(verdict, step=None, tag=None) -> Text:
    """pass -> green ✓, fail -> red ✗. With no verdict, fall back to the eval
    step status: RUNNING -> yellow ▸, FAILED -> red ✗, anything else
    (pending/completed-without-verdict/None) -> dim ·. A `tag` (e.g. "re-check")
    follows the glyph, dim. When the eval step has started, append a dim
    duration (elapsed while running, final once done)."""
    step_status = step.status if step else None
    if verdict == "pass":
        cell = Text("✓", style="green")
    elif verdict == "fail":
        cell = Text("✗", style="red")
    elif verdict == "blocked":
        cell = Text("⛔", style="yellow")
    elif step_status == TaskStatus.RUNNING:
        cell = Text("▸", style="yellow")
    elif step_status == TaskStatus.FAILED:
        cell = Text("✗", style="red")
    else:
        cell = Text("·", style="dim")
    if tag:
        cell.append(" ")
        cell.append(tag, style="dim")
    if step and step.started_at is not None:
        dur = format_duration(step.started_at, step.completed_at,
                              is_running=step.status == TaskStatus.RUNNING, now=None)
        cell.append(" ")
        cell.append(dur, style="dim")
    return cell


def _eval_gantt_bar(step, t0, span_secs, width=12, now=None) -> str:
    """Gantt bar for one eval step within the iteration's (t0, span). "" when
    the step never started or the span is degenerate. lead ░ + bar █, clamped
    to `width`. A stopped step with no recorded end gets no bar either."""
    if step is None or step.started_at is None or span_secs <= 0:
        return ""
    if step.completed_at is None and step.status != TaskStatus.RUNNING:
        return ""
    end = step.completed_at or (now or datetime.utcnow())
    start_off = (step.started_at - t0).total_seconds() / span_secs
    dur_frac = (end - step.started_at).total_seconds() / span_secs
    bar = max(1, round(dur_frac * width))
    lead = round(start_off * width)
    lead = min(lead, width - bar) if lead + bar > width else lead
    lead = max(0, lead)
    return "░" * lead + "█" * bar


def _current_stage(steps: list) -> Optional[str]:
    """Stage label from loop steps: the latest RUNNING step, else the latest
    step overall (steps arrive in execution order). 'plan'/'implement' verbatim;
    evaluate becomes 'eval · <dimension>'. None when there are no steps."""
    if not steps:
        return None
    step = next((s for s in reversed(steps) if s.status == TaskStatus.RUNNING),
                steps[-1])
    if step.step_type == "evaluate":
        return f"eval · {step.dimension or '?'}"
    return step.step_type


_CYCLE_NODES = ["plan", "implement", "eval"]


def loop_stage_breadcrumb(loop: Loop, steps: list = None) -> Text:
    """'plan → implement → eval' with the active cycle node bold+cyan, rest dim.
    Active node comes from _current_stage: 'plan'/'implement' map to themselves,
    any 'eval · <dim>' maps to 'eval'. No node is active when the loop isn't
    running or there's no current stage (terminal/pending loops show all dim)."""
    active = None
    if loop.status == JobStatus.RUNNING:
        stage = _current_stage(steps or [])
        if stage:
            active = "eval" if stage.startswith("eval") else stage
    line = Text()
    for i, node in enumerate(_CYCLE_NODES):
        if i:
            line.append(" → ", style="dim")
        line.append(node, style="bold cyan" if node == active else "dim")
    return line


def render_loop_progress_table(
    loop: Loop, journal_records: list, dimensions: list = None, steps: list = None
) -> Table:
    """Heatmap for `mini loop status`: dimensions down the rows, the last N
    iterations across as columns, one glyph cell per verdict. Built from
    journal records (loop_journal.read), NOT DB rows. Tolerates zero evaluate
    records — returns a placeholder table rather than raising.

    `dimensions` (ordered spec dimension names) seeds the row set so every
    dimension shows from iteration 0, with dim `·` cells until it has a verdict.
    When None, falls back to first-seen-in-journal order. Journal dimensions not
    in `dimensions` are still appended.

    `steps` supplies eval step state: no-verdict cells show ▸ (running) or a dim
    · (not dispatched / pending) based on the matching evaluate step's status.

    A pass carried over from iteration N (select: failed) shows a faded "✓ from N"
    with no duration or timeline bar, still counts toward the passing footer, and
    adds a caption explaining it. A re-check of one (its latest record has
    "recheck": true) shows "✓ re-check <duration>" and adds a caption line too.

    Columns are headed by iteration number; while the loop runs, the current
    iteration's column reads "N now" (_now_iteration).

    A RUNNING step of a loop that isn't running shows as stopped (shown_loop_steps)."""
    steps = shown_loop_steps(loop, steps or [])
    rows, iters = _pivot_evaluate(journal_records)
    carried = _carried_cells(journal_records)
    rechecked = _recheck_cells(journal_records)
    eval_steps = {(s.iteration, s.dimension): s
                  for s in steps if s.step_type == "evaluate"}
    # Iterations with eval steps but no journaled verdict yet still get a column,
    # so the column appears the moment a fresh iteration's eval fan-out starts.
    iters = sorted(set(iters) | {it for (it, _dim) in eval_steps})
    # Genuine no-data case: no dimensions AND no evaluations -> placeholder.
    if not iters and not dimensions:
        table = Table()
        table.add_column("dimension", style="cyan")
        table.add_column("status", style="dim")
        table.add_row("(no evaluations yet)", "—")
        return table

    # Row order: spec dimensions first (if given), then any journal-only dims.
    ordered = list(dimensions) if dimensions else []
    ordered += [d for d in rows if d not in ordered]

    # Same width->N math the Gantt uses; here it caps how many iteration columns fit.
    n = fit_width(40)
    # Zero-iteration seed: one column of dim cells until verdicts arrive.
    shown = iters[-n:] if iters else [None]
    now_it = _now_iteration(loop, steps)

    table = Table(show_footer=True)
    table.add_column("dimension", style="cyan", no_wrap=True, footer="passing")
    for it in shown:
        if it is None:
            header = "now" if loop.status == JobStatus.RUNNING else "—"
        else:
            header = _iteration_header(it, now_it)
        # Per-column footer: X/Y passes at this iteration, "—" when no verdicts.
        verdicts = [rows[d][it] for d in ordered if it in rows.get(d, {})]
        foot = f"{verdicts.count('pass')}/{len(ordered)}" if verdicts else "—"
        table.add_column(header, justify="center", footer=foot)
    table.add_column("timeline", justify="left", footer="")

    # Timeline (t0, span) for the current iteration only; blank if it has no
    # started eval steps (covers the None seed iteration -> no bars).
    now = datetime.utcnow()
    cur_it = shown[-1]
    # A carried-over pass ran nothing, so it neither shapes the span nor gets a bar;
    # nor does a stopped step with no recorded end.
    cur_eval = [eval_steps[(cur_it, d)] for d in ordered
                if (cur_it, d) in eval_steps and eval_steps[(cur_it, d)].started_at
                and (eval_steps[(cur_it, d)].completed_at
                     or eval_steps[(cur_it, d)].status == TaskStatus.RUNNING)
                and (d, cur_it) not in carried]
    if cur_eval:
        t0 = min(s.started_at for s in cur_eval)           # first eval start (leftmost)
        t_end = max((s.completed_at or now) for s in cur_eval)  # last eval end
        span_secs = (t_end - t0).total_seconds()
    else:
        t0, span_secs = None, 0

    def cell(dim, it) -> Text:
        if (dim, it) in carried:
            return Text(f"✓ from {carried[(dim, it)]}", style="dim green")
        return _verdict_cell(rows.get(dim, {}).get(it), eval_steps.get((it, dim)),
                             tag="re-check" if (dim, it) in rechecked else None)

    for dim in ordered:
        bar = ("" if (dim, cur_it) in carried
               else _eval_gantt_bar(eval_steps.get((cur_it, dim)), t0, span_secs, now=now))
        table.add_row(dim, *[cell(dim, it) for it in shown], bar)

    shown_cells = [(dim, it) for dim in ordered for it in shown]
    caption = []
    if any(c in carried for c in shown_cells):
        caption.append(_CARRIED_CAPTION)
    if any(c in rechecked and c not in carried for c in shown_cells):
        caption.append(_RECHECK_CAPTION)
    if caption:
        table.caption = "\n".join(caption)
    return table


def render_loop_stage_timing(loop: Loop, steps: list, dimensions: list = None,
                             journal_records=()) -> Table:
    """Companion to render_loop_progress_table: stage durations per iteration.
    Rows are plan/implement/eval/iter total; columns are the SAME last-N
    iterations the heatmap shows (derived from steps' `.iteration`). Each cell is
    format_duration over the matching steps' min(started_at)..max(completed_at);
    a still-running step shows elapsed, a stopped one with no recorded end "—".
    Headers follow the heatmap's rule: iteration numbers, "N now" on the
    running loop's current iteration. No footer row.

    A last "tokens" row holds each iteration's token total over its counted
    steps (tokens_total; `journal_records` mark carried-over passes), "—" when
    none reported."""
    steps = shown_loop_steps(loop, steps or [])
    if not steps:
        table = Table()
        table.add_column("stage", style="cyan", no_wrap=True)
        table.add_column("status", style="dim")
        table.add_row("(no timing yet)", "—")
        return table

    iters = sorted({s.iteration for s in steps})

    n = fit_width(40)
    shown = iters[-n:]
    now_it = _now_iteration(loop, steps)

    def cell(iteration, step_type):
        matched = [s for s in steps if s.iteration == iteration
                   and (step_type is None or s.step_type == step_type)]
        if not matched:
            return format_duration(None, None)
        starts = [s.started_at for s in matched if s.started_at]
        ends = [s.completed_at for s in matched]
        running = any(s.status == TaskStatus.RUNNING for s in matched)
        started = min(starts) if starts else None
        completed = None if running or None in ends else max(ends)
        return format_duration(started, completed, is_running=running)

    table = Table()
    table.add_column("stage", style="cyan", no_wrap=True)
    for it in shown:
        table.add_column(_iteration_header(it, now_it), justify="center")

    rows = [("plan", "plan"), ("implement", "implement"),
            ("eval", "evaluate"), ("iter total", None)]
    for label, step_type in rows:
        table.add_row(label, *[cell(it, step_type) for it in shown])

    counted = counted_loop_steps(steps, journal_records)
    table.add_row("tokens", *[
        format_tokens(tokens_total(s for s in counted if s.iteration == it)["total"])
        for it in shown
    ])
    return table


def loop_progress_summary(journal_records: list, dimensions: list = None) -> Optional[str]:
    """One-line note printed full-width below the heatmap (like the legend) —
    NOT the table caption, which rich centers+wraps to the narrow table width.
    Returns ONLY a note or None; the passing count now lives in the table footer.
    None when there's nothing to say; "no evaluations yet" pre-eval; a truncation
    note only when the table shows fewer columns than there are iterations."""
    rows, iters = _pivot_evaluate(journal_records)
    ordered = list(dimensions) if dimensions else []
    ordered += [d for d in rows if d not in ordered]
    if not ordered:
        return None
    if not iters:
        return "no evaluations yet"  # pre-eval: 'passing 0/N' misreads as failure
    n = fit_width(40)
    shown = iters[-n:]
    if len(shown) == len(iters):
        return None  # not truncated — 'Iteration n/max' already shows the count
    return f"showing last {len(shown)} of {len(iters)} iterations"
