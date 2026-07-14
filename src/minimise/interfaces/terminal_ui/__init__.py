"""Terminal UI formatting for job status display.

Re-exports the flat surface callers (cli/job.py, cli/loop.py, tests) import.
"""

from ._shared import (
    _now_or_default,
    fit_width,
    format_duration,
    get_status_color,
    humanize_duration,
)
from .gantt import (
    Step,
    _hook_steps,
    _match_hook,
    build_steps,
    layout_projected_bars,
    project_steps,
    render_execution_table_with_gantt,
    render_gantt_bar,
)
from .loop import (
    _CYCLE_NODES,
    _current_stage,
    _eval_gantt_bar,
    _pivot_evaluate,
    _verdict_cell,
    loop_progress_summary,
    loop_stage_breadcrumb,
    render_loop_progress_table,
    render_loop_stage_timing,
)

__all__ = [
    "_now_or_default", "fit_width", "format_duration", "get_status_color",
    "humanize_duration",
    "Step", "_match_hook", "_hook_steps", "build_steps", "render_gantt_bar",
    "project_steps", "layout_projected_bars", "render_execution_table_with_gantt",
    "_pivot_evaluate", "_verdict_cell", "_eval_gantt_bar", "_current_stage",
    "_CYCLE_NODES", "loop_stage_breadcrumb", "render_loop_progress_table",
    "render_loop_stage_timing", "loop_progress_summary",
]
