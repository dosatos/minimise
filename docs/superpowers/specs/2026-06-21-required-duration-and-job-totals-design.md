# Design: Required `estimated_duration_min` + Job-Level Duration Totals

**Date:** 2026-06-21
**Status:** Approved (pending spec review)

## Problem

`estimated_duration_min` is a per-task field, but its "required" status is only
half-enforced and its job-level total is only half-displayed:

- The **plan validator** errors when the key is *absent*, but does not check that the
  value is a positive integer (a string or `0` passes today).
- The **model field** is `Optional[int] = None`, and the **SQLite column** is nullable
  (`DEFAULT NULL`). So the type system and storage layer both permit a missing value,
  contradicting the validator's intent.
- **`mini job list --format json`**: each job object has no duration field at all
  (the *table* already shows a "Duration (min)" column).
- **`mini job status --format json`**: per-task `estimated_duration_min` is present, but
  there is no **job-level total**.
- **`mini job status` table**: shows no duration total anywhere.

## Goals

1. Make `estimated_duration_min` genuinely **required and `> 0`** end-to-end:
   model, plan validator, and database all agree it is always a positive integer.
2. Surface the **job-level total** in both JSON commands and the `job status` table.

## Non-Goals

- No change to the per-task table rendering (Gantt) — already shows per-task duration.
- No change to the `job list` table column — already present and correct.
- No new CLI flags or commands.

---

## Part A — Enforce "required, > 0"

### A1. Plan validator (`plan_validator.py:103`)

Upgrade the existing presence check to also validate the value:

- Key must be present (existing behavior).
- Value must be an `int` (reject `bool`, since `bool` is an `int` subclass) and `> 0`.
- On failure, emit a `ValidationLevel.ERROR` with a clear message, e.g.
  `Task {i} 'estimated_duration_min' must be a positive integer (got {value!r})`.

This is the primary user-facing gate — every plan submitted via `mini job new`
passes through it.

### A2. Model field (`models.py:35`) — **field reordering required**

Change `estimated_duration_min: Optional[int] = field(default_factory=lambda: None)`
to a required `estimated_duration_min: int` (no default).

**Dataclass ordering constraint:** a field without a default cannot follow fields that
have defaults. The field is currently last (after `status`, `output`, `created_at`,
etc., which all have defaults). It must **move up** to immediately after `description`
(the last no-default field), giving field order:
`id, job_id, name, description, estimated_duration_min, status, output, ...`

**Blast radius:** This reorders positional args. All construction sites must pass
`estimated_duration_min` as a keyword (or in the new position). Audit:
- **src/** (3 sites): update each to pass the value.
- **tests/** (~55 `Task(...)` constructions): every one that omits the field must add
  `estimated_duration_min=<n>` (use a sensible non-zero default like `5` or `30`).
  Keyword construction is the norm in this codebase, so reordering is safe; the only
  required change is *adding the now-mandatory argument*.

### A3. Database — backfill + NOT NULL (`database.py`)

Two steps, in order:

1. **Backfill** (in the existing migration block, ~line 73, after the add-column step):
   ```sql
   UPDATE tasks SET estimated_duration_min = 5 WHERE estimated_duration_min IS NULL
   ```
   Legacy rows with `NULL` become `5` (the agreed default).

2. **NOT NULL column.** SQLite cannot `ALTER COLUMN ... SET NOT NULL` in place. Make the
   fresh-schema `CREATE TABLE` (`database.py:52`) declare
   `estimated_duration_min INTEGER NOT NULL`. For **existing** databases, perform the
   standard SQLite table-rebuild migration *only if* the live column still permits NULL:
   create `tasks_new` with the NOT NULL column, `INSERT INTO tasks_new SELECT ... FROM tasks`
   (safe because backfill already removed all NULLs), drop `tasks`, rename `tasks_new`.
   Guard it so it runs at most once (e.g. check whether the column is already NOT NULL via
   `PRAGMA table_info`).

   **Simpler acceptable alternative** if the rebuild proves fragile: keep the column
   physically `DEFAULT NULL` but rely on the backfill + model + validator for the
   guarantee. Decision deferred to implementation — prefer the true NOT NULL rebuild; fall
   back only if the rebuild risks data loss. Whichever is chosen, it must be stated in the
   task's completion notes.

---

## Part B — Display job-level totals

The total is `sum(t.estimated_duration_min for t in tasks)`. With Part A, every task has
a positive int, so the sum is always a positive int when tasks exist, and `0` when there
are no tasks. **JSON placement: nested inside the existing `tasks` object** (per user
decision).

### B1. `mini job list --format json` (`cli.py:231`)

Add `estimated_duration_min` to the per-job `tasks` object:
```json
"tasks": { "total": 3, "completed": 1, "estimated_duration_min": 75 }
```
Compute from the `tasks` already fetched at `cli.py:255` (reuse, don't re-query).

### B2. `mini job status --format json` (`cli.py:336`)

Add `estimated_duration_min` to the job-level `tasks`... but note `job status` JSON
currently emits `tasks` as a **list** (`tasks_data`), not a summary object. To nest the
total "inside the tasks object" without breaking the existing per-task list, add a
sibling summary key alongside the list rather than mutating the list:
```json
{
  "id": "...",
  "tasks": [ {per-task...}, ... ],
  "tasks_summary": { "total": 3, "completed": 1, "estimated_duration_min": 75 }
}
```
**Open nuance:** `job list` nests under `tasks` (an object there), but `job status`
already uses `tasks` for the *list*. Implementation must not overwrite the task list.
Use `tasks_summary` for the status command's job-level totals. (This keeps backward
compatibility with existing `tasks` list consumers and existing tests at
`test_cli.py:339`.)

### B3. `mini job status` table (`cli.py:340`)

Add a job-level line among the other details (after `Status:` / near `Created:`):
```
Estimated Duration: 75 min
```
Render from the same sum. When there are no tasks, show `0 min` (or omit — show `0 min`
for consistency with the always-numeric guarantee).

---

## Testing

Follow TDD. New/updated tests in `tests/test_cli.py` and `tests/test_plan_validator.py`:

- **Validator:** rejects `estimated_duration_min` that is `0`, negative, a string, or a
  bool; accepts a positive int.
- **`job list` JSON:** asserts `tasks.estimated_duration_min` equals the sum of task
  estimates; equals `0` for a job with no tasks.
- **`job status` JSON:** asserts `tasks_summary.estimated_duration_min` equals the sum;
  existing `tasks` list and per-task fields remain unchanged.
- **`job status` table:** asserts the rendered output contains the duration total line.
- **DB migration:** a DB seeded with a NULL-duration row gets backfilled to `5`; column
  is NOT NULL after migration (if the rebuild path is taken).
- **Model:** constructing `Task(...)` without `estimated_duration_min` now raises
  `TypeError` (the field is mandatory).
- Update all existing `Task(...)` constructions in the suite to pass the field.

Full suite must stay green (currently 226 passing).

## Risks / Gotchas

- **Field reordering** (A2) is the highest-churn change: ~55 test sites. Mechanical but
  must be thorough — a missed site is a hard `TypeError` at construction (loud, not
  silent, which is good).
- **SQLite NOT NULL** (A3) needs a table rebuild; backfill MUST run first or the rebuild's
  `INSERT ... SELECT` would violate the constraint.
- **`job status` `tasks` key collision** (B2): do not clobber the existing task list;
  use `tasks_summary` for the job-level total there.
- Git must be clean before `mini job new` (dogfooding constraint).
