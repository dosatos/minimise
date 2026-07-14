"""Terminal UI formatting for job status display."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.text import Text
from minimise.models import Job, Task, JobStatus, TaskStatus, Plan, Loop


@dataclass
class Step:
    """One Gantt row — a task attempt or a hook. Name/estimate from the plan,
    status/timing from the execution (PENDING when none)."""
    name: str
    estimate: Optional[int]
    status: TaskStatus
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    is_hook: bool = False
    exit_reason: str = ""
    assignee: str = ""


def _match_hook(execs, execution_type, task_id, hook_name):
    return next((e for e in execs if e.execution_type == execution_type
                 and e.task_id == task_id and e.hook_name == hook_name), None)


def _hook_steps(hooks, execs, execution_type, task_id):
    steps = []
    for hook in hooks:
        ex = _match_hook(execs, execution_type, task_id, hook.name)
        steps.append(Step(
            name=hook.name, estimate=hook.estimated_duration_min,
            status=ex.status if ex else TaskStatus.PENDING,
            started_at=ex.started_at if ex else None,
            ended_at=ex.completed_at if ex else None,
            is_hook=True,
        ))
    return steps


def build_steps(plan: Plan, tasks: list, executions: list) -> list:
    """Assemble Gantt rows in plan order: plan.pre_hooks, then per task
    (pre_hooks -> attempts -> post_hooks), then plan.post_hooks."""
    steps = _hook_steps(plan.pre_hooks, executions, "pre_plan", None)
    for idx, ptask in enumerate(plan.tasks):
        task = tasks[idx] if idx < len(tasks) else None
        task_id = task.id if task else None
        steps += _hook_steps(ptask.pre_hooks, executions, "pre_task", task_id)

        attempts = sorted(
            (e for e in executions if e.execution_type == "task" and e.task_id == task_id),
            key=lambda e: e.attempt,
        )
        assignee = (task.assignee or "") if task else ""
        if attempts:
            for e in attempts:
                steps.append(Step(name=f"{ptask.name}  · try {e.attempt + 1}",
                                  estimate=ptask.estimated_duration_min, status=e.status,
                                  started_at=e.started_at, ended_at=e.completed_at,
                                  exit_reason=e.exit_reason or "", assignee=assignee))
        else:
            steps.append(Step(name=ptask.name, estimate=ptask.estimated_duration_min,
                              status=TaskStatus.PENDING, assignee=assignee))

        steps += _hook_steps(ptask.post_hooks, executions, "post_task", task_id)
    steps += _hook_steps(plan.post_hooks, executions, "post_plan", None)
    return steps


def _now_or_default(now: Optional[datetime]) -> datetime:
    """Return now, or the current UTC time if none was supplied."""
    return now or datetime.utcnow()


def humanize_duration(total_seconds: float) -> str:
    """
    Format duration as human-readable string with appropriate units.

    Args:
        total_seconds: Duration in seconds

    Returns:
        Formatted duration string (e.g., "2m 35s", "1h 30m", "1d 0h 0m")
    """
    if total_seconds < 1:
        return f"{int(total_seconds * 1000)}ms"
    elif total_seconds < 60:
        return f"{total_seconds:.1f}s"
    elif total_seconds < 3600:
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        return f"{minutes}m {seconds}s"
    elif total_seconds < 86400:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
    else:
        days = int(total_seconds // 86400)
        hours = int((total_seconds % 86400) // 3600)
        minutes = int((total_seconds % 3600) // 60)
        return f"{days}d {hours}h {minutes}m"


def get_status_color(status) -> str:
    """Get color for status badge."""
    if isinstance(status, (JobStatus, TaskStatus)):
        status_value = status.value
    else:
        status_value = str(status)

    colors = {
        "pending": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "stopped": "magenta",
    }
    return colors.get(status_value, "white")


def format_duration(
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
    is_running: bool = False,
    now: Optional[datetime] = None
) -> str:
    """
    Format task duration as human-readable string.

    Args:
        started_at: Task start time
        completed_at: Task completion time
        is_running: Whether task is currently running (shows elapsed time)
        now: Current time for elapsed calculation (defaults to now)

    Returns:
        Formatted duration string (e.g., "1.2s", "0.8s", "15.2s")
    """
    if not started_at:
        return "—"

    if is_running and not completed_at:
        now = _now_or_default(now)
        elapsed = (now - started_at).total_seconds()
        return humanize_duration(elapsed)

    if not completed_at:
        return "—"

    duration = (completed_at - started_at).total_seconds()
    return humanize_duration(duration)


def render_gantt_bar(
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
    job_started_at: Optional[datetime],
    job_completed_at: Optional[datetime],
    bar_width: int = 28,
    is_running: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """
    Render a Gantt-style progress bar showing task timing relative to job.

    Args:
        started_at: Task start time
        completed_at: Task completion time
        job_started_at: Job start time (for relative positioning)
        job_completed_at: Job completion time (for timeline scaling)
        bar_width: Width of the bar in characters
        is_running: Whether task is currently running
        now: Current time for running task calculation

    Returns:
        ASCII bar string (e.g., "████░░░░░")
    """
    if not started_at or not job_started_at:
        return "—"

    now = _now_or_default(now)

    if is_running and not completed_at:
        task_end = now
    elif not completed_at:
        return "—"
    else:
        task_end = completed_at

    # Use current time as scaling reference if job is still running
    job_end_for_scaling = job_completed_at if job_completed_at else now

    # Calculate total job duration
    job_duration = (job_end_for_scaling - job_started_at).total_seconds()
    if job_duration <= 0:
        return "—"

    # Calculate task position and duration relative to job
    task_start_offset = (started_at - job_started_at).total_seconds()
    task_end_offset = (task_end - job_started_at).total_seconds()

    # Clamp to job timeline
    task_start_offset = max(0, task_start_offset)
    task_end_offset = min(job_duration, task_end_offset)

    # Convert to bar positions
    start_pos = int((task_start_offset / job_duration) * bar_width)
    end_pos = int((task_end_offset / job_duration) * bar_width)

    # Ensure at least 1 character for visibility
    if start_pos == end_pos:
        end_pos = min(start_pos + 1, bar_width)

    # Build bar
    return "".join(
        "█" if start_pos <= i < end_pos else "░" for i in range(bar_width)
    )


def project_steps(steps, job_start, now):
    """Project the whole plan onto one shared timeline (seconds from job_start).

    Walks steps in order carrying a projected cursor so pending work chains
    after the last known end. Returns (placements, total_secs) where each
    placement is (start_off, actual_end_off, proj_end_off) in seconds. The
    timeline spans the projected end of the last step, so bars fill the full
    width regardless of when the job is viewed."""
    placements = []
    cursor = 0.0
    now_off = (now - job_start).total_seconds()
    # per-step start offset (None if not started yet), index-aligned with steps
    started_offs = [(s.started_at - job_start).total_seconds() if s.started_at
                    else None for s in steps]
    for i, step in enumerate(steps):
        est = (step.estimate or 0) * 60
        done = step.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)
        if done and step.started_at and step.ended_at:
            start_off = (step.started_at - job_start).total_seconds()
            actual_end_off = proj_end_off = (step.ended_at - job_start).total_seconds()
        elif step.status == TaskStatus.RUNNING and step.started_at:
            start_off = (step.started_at - job_start).total_seconds()
            # a still-RUNNING step's solid bar must not paint past the start of
            # a later step that has already begun (e.g. its post_task hook)
            next_started = min((o for o in started_offs[i + 1:] if o is not None),
                               default=float("inf"))
            actual_end_off = min(now_off, next_started)
            proj_end_off = max(actual_end_off, start_off + est)
        else:  # PENDING (or no start time)
            start_off = cursor
            actual_end_off = start_off
            proj_end_off = start_off + est
        start_off = max(0, start_off)
        actual_end_off = max(0, actual_end_off)
        proj_end_off = max(0, proj_end_off)
        cursor = max(cursor, proj_end_off)
        placements.append((start_off, actual_end_off, proj_end_off))
    total_secs = max(cursor, 1)  # never divide by zero
    return placements, total_secs


def layout_projected_bars(placements, total_secs, width=28):
    """Lay every step onto the shared timeline in ONE pass, so no column is
    claimed by two steps. A per-row renderer can't do this: two time-adjacent
    steps each narrower than a column both round into the boundary column and
    overlap. Here a left-to-right cursor gives each column a single owner and
    guarantees every step ≥1 column (else short steps vanish on a coarse scale).

    Each placement is (start_off, actual_end_off, proj_end_off) in seconds:
    solid █ for real elapsed [start, actual_end), light ░ for the projected
    remainder [actual_end, proj_end). Returns one bar string per placement.
    """
    scale = width / max(total_secs, 1)
    rows, next_free = [], 0
    for start_off, actual_end_off, proj_end_off in placements:
        # start no earlier than the last step's end — packs bars contiguously,
        # so rounding can never make two steps share a column.
        # ponytail: contiguous packing hides real idle gaps between steps;
        # add gap-preservation only if the timeline must show waiting time.
        s_col = min(max(next_free, int(start_off * scale)), width)
        e_col = min(max(int(round(proj_end_off * scale)), s_col + 1), width)
        # solid end: real elapsed only. Zero-width actual (pending / just
        # started) => no █, so pending never reads as running.
        a_col = min(max(int(round(actual_end_off * scale)), s_col + 1), e_col) \
            if actual_end_off > start_off else s_col
        rows.append(" " * s_col + "█" * (a_col - s_col)
                    + "░" * (e_col - a_col) + " " * (width - e_col))
        next_free = e_col
    return rows


def fit_width(chrome: int) -> int:
    """Size a bar to the terminal: give it whatever the other columns and chrome
    leave over (clamped), so it isn't cropped with "…" on narrow terminals.

    The Console import stays local: tests monkeypatch rich.console.Console at its
    source module, which only works if we look it up at call time.
    """
    from rich.console import Console
    return max(8, min(28, Console().width - chrome))


def render_execution_table_with_gantt(
    job: Job,
    tasks: list[Task],
    now: Optional[datetime] = None,
    executions: Optional[list] = None,
    plan: Optional[Plan] = None,
) -> Table:
    """
    Render task progress table with Duration, Expected, and Timeline (Gantt) columns.

    Args:
        job: Job object with timing info
        tasks: List of tasks to display
        now: Current time for elapsed calculation
        executions: Flat list[Execution] in job timeline order (from
            list_executions_for_job). It is THE source of rows — one row per
            execution, in the given order (NOT re-sorted). Covers task attempts
            AND plan/per-task hooks. Tasks with no execution yet get a PENDING
            placeholder row.

    Returns:
        Rich Table with Task Name, Status, Duration, Expected, and Timeline columns
    """
    now = _now_or_default(now)
    executions = executions or []
    bar_width = fit_width(66)

    table = Table()
    table.add_column("Task Name", style="cyan")
    table.add_column("Assignee", style="dim")
    table.add_column("Status", style="cyan")
    table.add_column("Duration", style="yellow")
    table.add_column("Expected", style="dim")
    table.add_column("Timeline (relative)", style="green", no_wrap=True)
    table.add_column("Type", style="dim")
    table.add_column("Reason", style="dim")

    def add_row(name, status, started_at, completed_at, estimated_duration_min, is_hook,
                timeline=None, exit_reason="", assignee=""):
        is_running = status == TaskStatus.RUNNING
        table.add_row(
            name,
            assignee or "",
            Text(status.value, style=get_status_color(status)),
            format_duration(started_at, completed_at, is_running=is_running, now=now),
            humanize_duration(estimated_duration_min * 60) if estimated_duration_min else "—",
            timeline if timeline is not None else render_gantt_bar(
                started_at,
                completed_at,
                job.started_at,
                job.completed_at,
                bar_width=bar_width,
                is_running=is_running,
                now=now,
            ),
            "hook" if is_hook else "task",
            exit_reason or "",
        )

    if plan is not None:
        steps = build_steps(plan, tasks, executions or [])
        bars = None
        if job.started_at:
            placements, total_secs = project_steps(steps, job.started_at, now)
            bars = layout_projected_bars(placements, total_secs, width=bar_width)
        for i, step in enumerate(steps):
            timeline = bars[i] if bars is not None else None
            add_row(step.name, step.status, step.started_at, step.ended_at,
                    step.estimate, step.is_hook, timeline=timeline,
                    exit_reason=getattr(step, "exit_reason", "") or "",
                    assignee=step.assignee)
        return table

    names = {t.id: t.name for t in tasks}
    task_est = {t.id: t.estimated_duration_min for t in tasks}
    task_assignee = {t.id: (t.assignee or "") for t in tasks}
    started_task_ids = set()
    for ex in executions:
        tname = names.get(ex.task_id, "")
        if ex.execution_type == "task":
            started_task_ids.add(ex.task_id)
            label, expected = f"{tname}  · try {ex.attempt + 1}", task_est.get(ex.task_id)
        elif ex.execution_type == "pre_plan":
            label, expected = "Pre-plan hook", None
        elif ex.execution_type == "post_plan":
            label, expected = "Post-plan hook", None
        elif ex.execution_type == "pre_task":
            label, expected = f"Pre-task hook  · {tname}", None
        else:  # post_task
            label, expected = f"Post-task hook  · {tname}", None
        add_row(label, ex.status, ex.started_at, ex.completed_at, expected,
                ex.execution_type != "task", exit_reason=ex.exit_reason or "",
                assignee=task_assignee.get(ex.task_id, "") if ex.execution_type == "task" else "")
    # PENDING tasks (no task-type execution yet) shown as placeholder rows in plan order.
    for task in tasks:
        if task.id not in started_task_ids:
            add_row(task.name, TaskStatus.PENDING, None, None, task.estimated_duration_min, False,
                    assignee=task.assignee or "")
    return table


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


def _verdict_cell(verdict, step=None) -> Text:
    """pass -> green ✓, fail -> red ✗. With no verdict, fall back to the eval
    step status: RUNNING -> yellow ▸, FAILED -> red ✗, anything else
    (pending/completed-without-verdict/None) -> dim ·. When the eval step has
    started, append a dim duration (elapsed while running, final once done)."""
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
    if step and step.started_at is not None:
        dur = format_duration(step.started_at, step.completed_at,
                              is_running=step.status == TaskStatus.RUNNING, now=None)
        cell.append(" ")
        cell.append(dur, style="dim")
    return cell


def _eval_gantt_bar(step, t0, span_secs, width=12, now=None) -> str:
    """Gantt bar for one eval step within the iteration's (t0, span). "" when
    the step never started or the span is degenerate. lead ░ + bar █, clamped
    to `width`."""
    if step is None or step.started_at is None or span_secs <= 0:
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
    · (not dispatched / pending) based on the matching evaluate step's status."""
    rows, iters = _pivot_evaluate(journal_records)
    eval_steps = {(s.iteration, s.dimension): s
                  for s in (steps or []) if s.step_type == "evaluate"}
    # Iterations with eval steps but no journaled verdict yet still get a column,
    # so "now" advances the moment a fresh iteration's eval fan-out starts.
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
    # Zero-iteration seed: one "now" column of dim cells until verdicts arrive.
    shown = iters[-n:] if iters else [None]

    table = Table(show_footer=True)
    table.add_column("dimension", style="cyan", no_wrap=True, footer="passing")
    for i, it in enumerate(shown):
        header = "now" if i == len(shown) - 1 else str(it)
        # Per-column footer: X/Y passes at this iteration, "—" when no verdicts.
        verdicts = [rows[d][it] for d in ordered if it in rows.get(d, {})]
        foot = f"{verdicts.count('pass')}/{len(ordered)}" if verdicts else "—"
        table.add_column(header, justify="center", footer=foot)
    table.add_column("timeline", justify="left", footer="")

    # Timeline (t0, span) for the current iteration only; blank if it has no
    # started eval steps (covers the None seed iteration -> no bars).
    now = datetime.utcnow()
    cur_it = shown[-1]
    cur_eval = [eval_steps[(cur_it, d)] for d in ordered
                if (cur_it, d) in eval_steps and eval_steps[(cur_it, d)].started_at]
    if cur_eval:
        t0 = min(s.started_at for s in cur_eval)           # first eval start (leftmost)
        t_end = max((s.completed_at or now) for s in cur_eval)  # last eval end
        span_secs = (t_end - t0).total_seconds()
    else:
        t0, span_secs = None, 0

    for dim in ordered:
        by_iter = rows.get(dim, {})
        bar = _eval_gantt_bar(eval_steps.get((cur_it, dim)), t0, span_secs, now=now)
        table.add_row(dim, *[_verdict_cell(by_iter.get(it), eval_steps.get((it, dim)))
                             for it in shown], bar)

    return table


def render_loop_stage_timing(loop: Loop, steps: list, dimensions: list = None) -> Table:
    """Companion to render_loop_progress_table: stage durations per iteration.
    Rows are plan/implement/eval/iter total; columns are the SAME last-N
    iterations the heatmap shows (derived from steps' `.iteration`). Each cell is
    format_duration over the matching steps' min(started_at)..max(completed_at);
    a still-running step (completed_at None) shows elapsed. No footer row."""
    if not steps:
        table = Table()
        table.add_column("stage", style="cyan", no_wrap=True)
        table.add_column("status", style="dim")
        table.add_row("(no timing yet)", "—")
        return table

    iters = sorted({s.iteration for s in steps})

    n = fit_width(40)
    shown = iters[-n:]

    def cell(iteration, step_type):
        matched = [s for s in steps if s.iteration == iteration
                   and (step_type is None or s.step_type == step_type)]
        if not matched:
            return format_duration(None, None)
        starts = [s.started_at for s in matched if s.started_at]
        running = any(s.completed_at is None for s in matched)
        started = min(starts) if starts else None
        completed = None if running else max(s.completed_at for s in matched)
        return format_duration(started, completed, is_running=running)

    table = Table()
    table.add_column("stage", style="cyan", no_wrap=True)
    for i, it in enumerate(shown):
        header = "now" if i == len(shown) - 1 else str(it)
        table.add_column(header, justify="center")

    rows = [("plan", "plan"), ("implement", "implement"),
            ("eval", "evaluate"), ("iter total", None)]
    for label, step_type in rows:
        table.add_row(label, *[cell(it, step_type) for it in shown])

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
