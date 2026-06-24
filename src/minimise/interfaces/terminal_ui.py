"""Terminal UI formatting for job status display."""

from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.text import Text
from minimise.models import Job, Task, JobStatus, TaskStatus


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


def render_task_table_with_gantt(job: Job, tasks: list[Task], now: Optional[datetime] = None) -> Table:
    """
    Render task progress table with Duration, Remaining Time, and Timeline (Gantt) columns.

    Args:
        job: Job object with timing info
        tasks: List of tasks to display
        now: Current time for elapsed calculation

    Returns:
        Rich Table with Task Name, Status, Duration, Remaining Time, and Timeline columns
    """
    now = _now_or_default(now)

    table = Table()
    table.add_column("Task Name", style="cyan")
    table.add_column("Status", style="cyan")
    table.add_column("Duration", style="yellow")
    table.add_column("Timeline (relative)", style="green")

    for task in tasks:
        is_running = task.status == TaskStatus.RUNNING
        status_text = Text(task.status.value, style=get_status_color(task.status))
        duration = format_duration(
            task.started_at,
            task.completed_at,
            is_running=is_running,
            now=now
        )
        gantt_bar = render_gantt_bar(
            task.started_at,
            task.completed_at,
            job.started_at,
            job.completed_at,
            is_running=is_running,
            now=now,
        )

        table.add_row(
            task.name,
            status_text,
            duration,
            gantt_bar,
        )

    return table
