"""Terminal UI formatting for job status display."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.text import Text
from minimise.models import Job, Task, JobStatus, TaskStatus, Plan


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


def render_projected_bar(start_off, actual_end_off, proj_end_off,
                         total_secs, width=28):
    """One row on the shared projected timeline: solid for actual elapsed,
    light for the projected estimate remaining."""
    cols = []
    for i in range(width):
        t = (i / width) * total_secs
        if start_off <= t < actual_end_off:
            cols.append("█")
        elif actual_end_off <= t < proj_end_off:
            cols.append("░")
        else:
            cols.append(" ")
    return "".join(cols)


def render_execution_table_with_gantt(
    job: Job,
    tasks: list[Task],
    now: Optional[datetime] = None,
    executions: Optional[list] = None,
    executions_by_task: Optional[dict] = None,
    plan: Optional[Plan] = None,
) -> Table:
    """
    Render task progress table with Duration, Expected, and Timeline (Gantt) columns.

    Args:
        job: Job object with timing info
        tasks: List of tasks to display
        now: Current time for elapsed calculation
        executions: Optional flat list[Execution] in job timeline order (from
            list_executions_for_job). When provided, it is THE source of rows —
            one row per execution, in the given order (NOT re-sorted) — and
            TAKES PRECEDENCE over executions_by_task. Covers task attempts AND
            plan/per-task hooks.
        executions_by_task: LEGACY map of task_id -> list[Execution]. Used only
            when ``executions`` is None. When a task has executions, one row is
            emitted per attempt; tasks without executions fall back to a single
            task-level row.

    Returns:
        Rich Table with Task Name, Status, Duration, Expected, and Timeline columns
    """
    now = _now_or_default(now)
    executions_by_task = executions_by_task or {}

    # Size the Gantt bar to the terminal: the other 7 columns + chrome need
    # ~66 cols, so give the rest to the bar (clamped) — otherwise a fixed-width
    # bar gets cropped with "…" on narrow terminals.
    from rich.console import Console
    bar_width = max(8, min(28, Console().width - 66))

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
        placements = total_secs = None
        if job.started_at:
            placements, total_secs = project_steps(steps, job.started_at, now)
        for i, step in enumerate(steps):
            timeline = None
            if placements is not None:
                s, a, p = placements[i]
                timeline = render_projected_bar(s, a, p, total_secs, width=bar_width)
            add_row(step.name, step.status, step.started_at, step.ended_at,
                    step.estimate, step.is_hook, timeline=timeline,
                    exit_reason=getattr(step, "exit_reason", "") or "",
                    assignee=step.assignee)
        return table

    if executions is not None:
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

    for task in tasks:
        task_executions = executions_by_task.get(task.id)
        if task_executions:
            for i, ex in enumerate(task_executions, start=1):
                add_row(
                    f"{task.name}  · try {i}",
                    ex.status,
                    ex.started_at,
                    ex.completed_at,
                    task.estimated_duration_min,
                    False,
                    assignee=task.assignee or "",
                )
        else:
            add_row(
                task.name,
                task.status,
                task.started_at,
                task.completed_at,
                task.estimated_duration_min,
                False,
                assignee=task.assignee or "",
            )

    return table
