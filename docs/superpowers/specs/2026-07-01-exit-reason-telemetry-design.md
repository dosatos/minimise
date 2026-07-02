# Design: `exit_reason` telemetry + retire the free-text `output` column

**Date:** 2026-07-01
**Roadmap items:** feat-6 (execution telemetry) + arch-7 (revisit failure-reason storage)
**Status:** approved for planning

## Problem

Per attempt we store only `started_at` / `completed_at`. When a task ends
`FAILED`, nothing distinguishes a 900s **timeout-kill** (`harness.py:161-166`)
from a real **agent error** (nonzero exit, `harness.py:179-182`) from a
**post-task hook failure** (`task_executor.py:80-88`). The reason is reverse-
engineered from raw transcripts (the painful 2026-06-25 investigation).

Separately (arch-7): the short failure reason is free text in the `output`
column and is the **only** copy — it never reaches `job.log`. arch-3 already
made `job.log` the canonical narration store for *successful* tasks
(`output=""`), so failures are the last thing keeping the column alive.

`duration_sec` is **already derivable** from `started_at`/`completed_at` (the
status table computes it today), so we do **not** store it. The only genuinely
new datum is a structured **`exit_reason`**.

## Goal

1. Capture a structured `exit_reason` per execution, set at the one place that
   authoritatively knows it.
2. Route all failure detail to `job.log` (the same channel success narration
   already uses), then **physically drop** the `output` column from both
   `tasks` and `executions`. `job.log` becomes the single narration store.

No new dependencies. No new store. Net simplification.

## Non-goals

- `model` / `harness_timeout_sec` capture, `mini job perf` command — roadmap
  defers these; not in this spec.
- Preserving legacy FAILED jobs' failure reasons. No customers; the reason for
  old jobs lived only in `output` and is lost on drop. Accepted.

## `exit_reason` vocabulary

A small closed set (plain strings, no enum — YAGNI):

| value         | set by            | when                                             |
|---------------|-------------------|--------------------------------------------------|
| `success`     | harness           | subprocess exit 0                                |
| `timeout`     | harness           | wall-clock deadline hit, child killed (`:161`)   |
| `agent_error` | harness           | nonzero exit / exception (`:182`, `:188`)        |
| `hook_failed` | task_executor     | post-task hook returned fail/retry-exhausted     |

Blank/NULL for rows written before this change and for hook executions
(hooks record their own pass/fail via status; a dedicated reason adds nothing).

## Changes

### 1. Harness — `HarnessResult.exit_reason`
`agents/harness.py`
- Add `exit_reason: str = ""` to `HarnessResult`.
- Set it at each return in `ClaudeCodeHarness.run`:
  `success` (line 180), `timeout` (166), `agent_error` (182, 188).
- The `error` free-text field stays — it carries the human detail
  (stderr / `"timeout after 900s"`) that gets logged, but is no longer the
  classification.

### 2. Executor — thread reason through, log failure detail
`orchestration/task_executor.py`
- `_invoke_claude_code` returns `(success, output, exit_reason)` instead of
  `(success, output)`. On failure it also has `result.error` to log.
- **On any failure path** (`record_attempt`, `mark_task_failed`), before/at
  the recorder call, write the failure detail to `job.log` via the same
  backend + `log_fields` (`type="task"`, `step=<task step incl. try suffix>`)
  the harness uses for success narration — so `task_narration` reconstructs it.
  The harness already logs its *streamed* assistant text; here we additionally
  log the terminal `error` string (stderr / timeout note) which is otherwise
  only in `result.error`. A timeout with no assistant output still yields a
  reconstructable line.
- Pass `exit_reason` into `record_attempt` / `mark_task_failed` /
  `record_completed` so it lands on the Execution row.
- The post-task-hook failure paths set `exit_reason="hook_failed"`.

### 3. Store — accept + persist `exit_reason`, stop writing free-text `output`
`storage/job_store.py`
- `record_attempt`, `record_completed`, `mark_task_failed`, `_close_attempt`
  accept `exit_reason` and pass it to the `Execution`.
- **Stop** writing `output=f"Attempt N failed: ..."` (line 107) and the FAILED
  reason (lines 120, 127) to the tasks table. Task-status updates no longer
  pass `output` at all.

### 4. Model + DB — add column, drop `output`
`models.py`
- `Execution` gains `exit_reason: Optional[str] = None`; add to `to_dict`.
- Remove `output` from `Task` and `Execution` dataclasses + their `to_dict`.

`storage/database.py`
- `SCHEMA_VERSION` 2 → 3.
- Add `exit_reason TEXT` to the `executions` CREATE, plus a guarded
  `ALTER TABLE executions ADD COLUMN exit_reason` for existing DBs (same
  pattern as `hook_name`, lines 261-263).
- **Drop `output`** from both `tasks` and `executions` via table-rebuild
  migration (the precedent already in this file, lines 209-233:
  `CREATE …_new` without the column → `INSERT … SELECT` shared columns →
  `DROP` → `RENAME`). Guard on "column still present" so it runs once.
- Row-mappers (`_row_to_task` ~45, `_row_to_execution` ~67), `create_task`
  (329-331), `save_task`/update (375-385), `save_execution` (413-419) drop
  `output`, and `save_execution` gains `exit_reason`.

### 5. hook_executor — drop the dead `output` write
`orchestration/hook_executor.py`
- Remove `ex.output = output` (line 46). Hook output already streams to
  `job.log` (lines 49-59); the column is going away.

### 6. Narration reader — always reconstruct from `job.log`
`interfaces/cli/_shared.py`
- `task_narration`: delete the `if task.output` short-circuit (79-80). Always
  reconstruct from `job.log`. (Signature unchanged; `task` param still used
  for `.name`.)

### 7. Surface it
`interfaces/terminal_ui.py` — `render_execution_table_with_gantt`
- Add a **"Reason"** column. `add_row` gains an `exit_reason` param; render the
  string (blank for success/hook/pending rows). All three row-emitting paths
  (plan-projected via `build_steps`, `executions`, legacy `executions_by_task`)
  pass it. `build_steps` (in the same module) carries `exit_reason` onto its
  step objects so the projected path has it.

`interfaces/cli/job.py`
- `job_status` JSON: add `exit_reason` per task/execution entry.
- `job_show`: print `exit_reason` next to `Status`.

`interfaces/api_server.py`
- `/jobs/<id>/tasks/<id>` returns `task.to_dict()`, which no longer has
  `output`. Documented contract change; acceptable (no customers). No code
  change needed beyond the model.

## Data flow (failure)

```
claude -p exits nonzero / times out
  → harness: HarnessResult(success=False, error="timeout after 900s",
                            exit_reason="timeout")
  → executor: log error string to job.log (step=task); record_attempt/
              mark_task_failed(task, exit_reason="timeout")
  → store: Execution(status=FAILED, exit_reason="timeout"); tasks.status=FAILED,
           NO output written
  → display: status table "Reason"=timeout; task_narration rebuilds detail
             from job.log
```

## Testing (assert-based, existing style)

- **Harness:** `exit_reason` is `success` / `timeout` / `agent_error` for the
  three return paths (fake/stub subprocess as existing harness tests do).
- **Executor:** on failure, `record_attempt`/`mark_task_failed` receive the
  right `exit_reason`; the failure detail is written to `job.log` and
  `task_narration` reconstructs it; no `output` is written.
- **Store/DB:** `save_execution` round-trips `exit_reason`; migration drops
  `output` from both tables and preserves other columns (rebuild path).
- **Narration:** update `tests/test_narration_reader.py` — the
  output-precedence case is gone; add a FAILED-reconstruction case.
- Update the flipped success assertion in `test_task_executor.py`
  (`assert not updated_task.output` → the attribute no longer exists; assert on
  `exit_reason` / job.log instead).

## Risks

- **Table rebuild on `tasks`/`executions`.** Precedent exists in-file; must run
  inside the existing `SAVEPOINT`/rollback discipline and be idempotent
  (guard on column presence). Foreign keys: rebuild `executions` and `tasks`
  carefully — `executions` FKs `tasks(id)`; rebuild children after parents or
  with FK enforcement off during migration (SQLite default is off).
- **`(success, output)` → `(success, output, exit_reason)`** touches every
  `_invoke_claude_code` caller — there is one. Grep to confirm before edit.
