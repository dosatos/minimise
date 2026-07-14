"""Primitives shared by the job Gantt and the loop heatmap."""

from datetime import datetime
from typing import Optional

from minimise.models import JobStatus, TaskStatus


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


def fit_width(chrome: int) -> int:
    """Size a bar to the terminal: give it whatever the other columns and chrome
    leave over (clamped), so it isn't cropped with "…" on narrow terminals.

    The Console import stays local: tests monkeypatch rich.console.Console at its
    source module, which only works if we look it up at call time.
    """
    from rich.console import Console
    return max(8, min(28, Console().width - chrome))
