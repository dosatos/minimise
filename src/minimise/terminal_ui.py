"""Terminal UI formatting for job status display."""

from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.text import Text
from minimise.models import Job, Task, JobStatus, TaskStatus


def get_status_color(status) -> str:
    """Get color for status badge."""
    if isinstance(status, JobStatus) or isinstance(status, TaskStatus):
        status_value = status.value
    else:
        status_value = str(status)

    colors = {
        "pending": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
    }
    return colors.get(status_value, "white")


def format_duration(started_at: Optional[datetime], completed_at: Optional[datetime]) -> str:
    """
    Format task duration as human-readable string.

    Args:
        started_at: Task start time
        completed_at: Task completion time

    Returns:
        Formatted duration string (e.g., "1.2s", "0.8s")
    """
    if not started_at or not completed_at:
        return "—"

    duration = (completed_at - started_at).total_seconds()

    if duration < 1:
        return f"{int(duration * 1000)}ms"
    else:
        return f"{duration:.1f}s"


def render_gantt_bar(
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
    job_started_at: Optional[datetime],
    job_completed_at: Optional[datetime],
    bar_width: int = 28,
) -> str:
    """
    Render a Gantt-style progress bar showing task timing relative to job.

    Args:
        started_at: Task start time
        completed_at: Task completion time
        job_started_at: Job start time (for relative positioning)
        job_completed_at: Job completion time (for timeline scaling)
        bar_width: Width of the bar in characters

    Returns:
        ASCII bar string (e.g., "████░░░░░")
    """
    if not all([started_at, completed_at, job_started_at, job_completed_at]):
        return "—"

    # Calculate total job duration
    job_duration = (job_completed_at - job_started_at).total_seconds()
    if job_duration <= 0:
        return "—"

    # Calculate task position and duration relative to job
    task_start_offset = (started_at - job_started_at).total_seconds()
    task_end_offset = (completed_at - job_started_at).total_seconds()

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
    bar = []
    for i in range(bar_width):
        if i < start_pos:
            bar.append("░")
        elif i < end_pos:
            bar.append("█")
        else:
            bar.append("░")

    return "".join(bar)


def render_task_table_with_gantt(job: Job, tasks: list[Task]) -> Table:
    """
    Render task progress table with Duration and Timeline (Gantt) columns.

    Args:
        job: Job object with timing info
        tasks: List of tasks to display

    Returns:
        Rich Table with Task Name, Status, Duration, and Timeline columns
    """
    table = Table()
    table.add_column("Task Name", style="cyan")
    table.add_column("Status", style="cyan")
    table.add_column("Duration", style="yellow")
    table.add_column("Timeline (relative)", style="green")

    for task in tasks:
        status_text = Text(task.status.value, style=get_status_color(task.status))
        duration = format_duration(task.started_at, task.completed_at)
        gantt_bar = render_gantt_bar(
            task.started_at,
            task.completed_at,
            job.started_at,
            job.completed_at,
        )

        table.add_row(
            task.name,
            status_text,
            duration,
            gantt_bar,
        )

    return table
