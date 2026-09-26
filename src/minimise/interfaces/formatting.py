"""Formatting helpers shared across terminal and web interfaces."""

from datetime import datetime
from typing import Optional


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
