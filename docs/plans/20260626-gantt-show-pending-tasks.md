# Show PENDING Tasks in the Job-Status Gantt

## Overview

The `mini job status` Gantt currently only renders tasks that already have an
Execution row. Tasks that have not yet started (PENDING) are invisible, so a
running job appears to grow its task list one row at a time as each task becomes
active. The view should show the full set of planned tasks up front — PENDING
tasks rendered as empty/greyed rows with their expected duration — so the user
sees the whole plan and remaining work at a glance.

## Context

- Impacted component: `src/minimise/interfaces/terminal_ui.py` only.
- Root cause: when an `executions` list is passed, the renderer iterates only
  over that list (one row per attempt + hooks) and returns early, so a task with
  zero executions emits no row. The lower fallback loop already emits a
  placeholder row for tasks with no executions, but the executions path never
  reaches it.
- Constraint: keep one-row-per-attempt for tasks that have started; do not change
  plan/per-task hook rows. PENDING rows use the task's name +
  `estimated_duration_min` and an empty timeline bar, ordered by plan position.
- Adopted from roadmap item `feat-5-gantt-show-pending-tasks`.

## Development Approach

- Testing approach: TDD — write a failing test that a PENDING task appears in the
  rendered rows when an executions list is supplied, then implement.
- Complete the task fully before moving on.
- Update this plan if scope changes during implementation.

## Testing Strategy

- Unit test required for the new placeholder-row behavior.
- Run the full project test suite (`pytest tests/ -q`) after the change — all
  tests must pass before completion.

## Technical Details

- In the `executions is not None` branch, after emitting rows for all executions,
  also emit a placeholder row for every task in `tasks` whose id never appeared in
  the executions list (zero `execution_type == "task"` rows).
- Placeholder row uses: task name, status PENDING, no start/complete timestamps,
  `estimated_duration_min` as the expected column, empty Gantt bar.
- Preserve plan order (iterate `tasks` in order, skip those already shown).

## Implementation Steps

### Task 1: Render PENDING tasks as placeholder rows in the executions path

- [x] Identify which task ids have at least one `execution_type == "task"` row in
      the supplied executions list
- [x] After the executions loop (before `return table`), emit a placeholder row
      for every task in `tasks` with no task-type execution, in plan order, using
      the task name, PENDING status, empty timestamps, and estimated duration
- [x] write tests for new functionality — assert a PENDING task appears as a row
      when an executions list containing only other tasks is rendered
- [x] run project tests - must pass before next task

### Task 2: Verify acceptance criteria

- [x] verify all requirements from Overview are implemented (full plan visible
      from the first status call; started tasks keep one row per attempt)
- [x] run full project test suite
- [x] run project linter - all issues must be fixed (no linter configured/installed;
      pytest is the project gate per CLAUDE.md — skipped, not applicable)
