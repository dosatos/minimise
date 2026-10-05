"""Job timeline rendering — the execution table with its Gantt column."""

from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.text import Text
from minimise.interfaces.timeline import (  # noqa: F401 — re-exported
    Step,
    _hook_steps,
    _match_hook,
    build_steps,
    project_steps,
)
from minimise.models import Job, Task, TaskStatus, Plan

from ._shared import (
    _now_or_default,
    fit_width,
    format_duration,
    get_status_color,
    humanize_duration,
)


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
            add_row(step.label, step.status, step.started_at, step.ended_at,
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
