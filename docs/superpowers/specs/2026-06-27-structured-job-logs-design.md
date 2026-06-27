# Structured Job Logs (CloudWatch Insights-style queries)

## Problem

`job.log` is flat text today — `[timestamp] <chunk>` lines plus `--- task ... ---`
banners. It's an opaque blob: you can't isolate one execution, limit, or sort.
We want the CloudWatch Logs Insights experience.

## Solution

Make each log line a JSON record (JSONL), and give `mini job logs` an Insights-style
`--query`. The query parses into an engine-neutral `LogQuery` IR run by a swappable
`JobLogBackend`, so the engine (JSONL now; DuckDB or CloudWatch later) can change
without touching the CLI, harness, or query syntax.

## Record format — JSONL

```json
{"timestamp":"2026-06-27T01:15:02","execution_id":"job_id#job-ab12#type#task#task#task-9f#attempt#1","type":"task","level":"info","message":"Running pytest..."}
```

| field | source |
|---|---|
| `timestamp` | write time |
| `execution_id` | `Execution.execution_id` (carries job/type/task/attempt) |
| `type` | `Execution.execution_type` — `task`/`pre_task`/`post_task` |
| `level` | write call, default `"info"` (reserved for future orchestrator logs) |
| `message` | assistant chunk |

`task`/`attempt` aren't separate fields — they're inside `execution_id`. Slice a
task's attempts with `filter execution_id like "task-9f"`.

Lines that fail `json.loads` render as `{"message": <raw line>}`, so old flat-text
logs still display — no migration. The `--- task ... ---` banner write is removed
(the per-line `execution_id` replaces it).

## Architecture

```
--query str ─parse─▶ LogQuery (IR) ─▶ JobLogBackend ─┬─ JsonlLogBackend (now)
                                                      └─ DuckDbLogBackend (future)
```

**`LogQuery`** — the engine-neutral parsed form: `fields`, `filters`
(predicates + `and`/`or`), `sort_key`/`sort_desc`, `limit`.

**One rule that keeps the backend swappable:** the IR encodes *intent, not dialect*.
A `like` predicate means "substring" — never a raw SQL `%x%` or `/regex/`. Each
backend renders intent into its own syntax. Leak dialect into the IR and the future
DuckDB translation breaks.

**`JobLogBackend`** — two methods, the swap seam:
- `record(log_path, fields, text, level)` — write one line
- `search(log_path, query) -> Iterator[dict]` — read

Injected via constructor. Adding DuckDB later = a new class reading the same JSONL
file; nothing else changes.

**`JsonlLogBackend`** (only impl now): `record()` appends a JSON line; `search()`
does `json.loads` per line then applies filter → sort → limit (≈30 lines of stdlib).

## Query language — Insights surface

Parsed with `pyparsing` (already installed).

```
mini job logs job-ab12 --query '
  fields @timestamp, message
  | filter type = "task" and execution_id like "task-9f"
  | sort @timestamp desc
  | limit 20'
```

| verb | notes |
|---|---|
| `fields a, b, @message` | only these print, in order. Omit → all. `@timestamp`→`timestamp`, `@message`→whole record |
| `filter f = "v" [and\|or g != "w"] [h like "sub"]` | `=`,`!=`,`like`(substring); `and`/`or` left-to-right, no parens |
| `sort @timestamp asc\|desc` | one key; default `timestamp asc` |
| `limit N` | |

Verbs optional; applied filter→sort→limit→fields regardless of order. Bad syntax →
clear error, exit 1.

## Wiring

- **Harness** stays ignorant of `Execution`. Constructed with a `JobLogBackend`
  (default `JsonlLogBackend`); `run(..., log_fields=None)` calls
  `backend.record(...)` per chunk. `log_fields=None` (e.g. `PlanReviewer`) writes
  nothing — unchanged.
- **`task_executor`** owns identity: passes
  `log_fields={"execution_id": ex.execution_id, "type": ex.execution_type}` and the
  per-job `log_path`; drops the banner write.
- **`mini job logs`**: no `--query` → unchanged (prints everything). `--query` →
  parse → search → render projecting exactly `fields`. `--json` → raw matching JSONL
  for `jq`. `-f` → tail; with `--query` the **filter** applies per new line,
  `sort`/`limit` are ignored on a live stream (one-line notice).

## Scope boundaries (YAGNI)

Not building: `stats`/aggregation, `parse`, regex, parentheses; no DuckDB dep
(~20MB) now; no orchestrator log records yet; no DB table; no new runtime dep
(`pyparsing` + `rich` + stdlib only).

**Why these are the right cuts:** for the 4 scoped verbs, Insights→SQL is ~1:1
(`filter→WHERE`, `sort→ORDER BY`, `limit→LIMIT`, `fields→SELECT`), so a future
DuckDB backend is trivial. Divergence only starts with `stats` (CloudWatch grouping
≠ SQL `GROUP BY`) and `parse` — those are the seams that would stretch, so we punt
them deliberately. Insights is also the smaller language: anything in it maps to
SQL, not vice-versa, so committing the *user surface* to Insights keeps any future
engine able to satisfy it.

## Testing (TDD)

- `test_logquery.py`: parse strings → expected `LogQuery` (+ bad-syntax errors);
  apply over canned records — filter (`=`/`!=`/`like`, `and`/`or`), sort, limit,
  projection, `@timestamp`/`@message`, non-JSON line tolerated.
- `test_harness.py`: chunk written as JSON line with merged `log_fields`;
  `log_fields=None` writes nothing.
- `test_cli.py`: no-query unchanged; `--query` filters/sorts/limits/projects;
  `--json` passthrough; `-f` filter live; "no logs yet".

## Files

- `src/minimise/logging/` (new): `log_query.py` (IR + parser), `backend.py`
  (ABC + `JsonlLogBackend`).
- `agents/harness.py`, `orchestration/task_executor.py`,
  `interfaces/cli/job.py` (modified).
