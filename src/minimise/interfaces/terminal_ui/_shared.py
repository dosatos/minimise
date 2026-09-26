"""Terminal primitives shared by the job Gantt and loop heatmap."""

from minimise.interfaces.formatting import (
    _now_or_default,
    format_duration,
    humanize_duration,
)
from minimise.models import JobStatus, TaskStatus


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


def fit_width(chrome: int) -> int:
    """Size a bar to the terminal: give it whatever the other columns and chrome
    leave over (clamped), so it isn't cropped with "…" on narrow terminals.

    The Console import stays local: tests monkeypatch rich.console.Console at its
    source module, which only works if we look it up at call time.
    """
    from rich.console import Console
    return max(8, min(28, Console().width - chrome))
