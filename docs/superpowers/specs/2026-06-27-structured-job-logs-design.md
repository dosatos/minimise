# Structured Job Logs (CloudWatch Insights-style queries)

## Overview

Today `job.log` is flat text — `[timestamp] <assistant chunk>` lines written by
the harness (`harness.py` `_read_stdout`), plus `--- task <id> attempt <n> ---`
banner lines from `task_executor`. `mini job logs` dumps the whole file or tails
it with `-f`. The file is an opaque blob: you cannot isolate one execution,
limit, or sort — the CloudWatch Logs Insights experience the user is used to.

This makes each log line a **structured JSON record** and gives `mini job logs`
a CloudWatch Insights-style `--query` (`fields | filter | sort | limit`). The
query is parsed into an engine-neutral `LogQuery` IR and run by a swappable
`JobLogBackend`, so the storage/query engine (JSONL now, DuckDB or CloudWatch
Logs later) can change without touching the CLI, the harness, or the query
syntax.

## Goals / non-goals

**Goals**
- Each harness write emits one structured JSON line (JSONL).
- `mini job logs --query '...'` filters/sorts/limits/projects, CloudWatch-faithful.
- Slice by execution (every harness invocation = one `Execution`, already a
  first-class entity with an opaque `execution_id`).
- Storage + query engine replaceable behind one interface, with the least future
  refactoring.

**Non-goals (YAGNI — do NOT build)**
- No `stats`/aggregation, `parse`, regex, functions, or parentheses in `--query`.
  These are the seams where CloudWatch and SQL genuinely diverge; punt until needed.
- No DuckDB dependency now (adds ~20MB). Documented as a future backend only.
- No orchestrator-emitted log records yet (only harness executions write). The
  `level` field exists for this but is always `"info"` today.
- No DB table for log lines; no migration of old flat-text logs (tolerated at read).
- No new runtime dependency: `pyparsing` (already transitively installed) +
  `rich` (already used) + stdlib only.

## Record format — JSONL

`job.log` becomes JSON Lines. One object per assistant chunk:

```json
{"timestamp":"2026-06-27T01:15:02.123","execution_id":"job_id#job-ab12#type#task#task#task-9f#attempt#1","type":"task","level":"info","message":"Running pytest..."}
```

| field | source | notes |
|---|---|---|
| `timestamp` | `datetime.now().isoformat()` (write time) | |
| `execution_id` | `Execution.execution_id` | opaque; carries job/type/task/attempt |
| `type` | `Execution.execution_type` | `task` / `pre_task` / `post_task` |
| `level` | write call, default `"info"` | reserved for future orchestrator logs |
| `message` | assistant text chunk | today's payload |

`task` and `attempt` are NOT separate fields — they live inside `execution_id`.
Filtering a task's attempts uses `filter execution_id like "task-9f"`.

**Back-compat:** any line that fails `json.loads` renders as `{"message": <raw line>}`
so old flat-text `job.log` files and stray non-JSON still display. No migration.

The `--- task ... attempt ... ---` banner write in `task_executor` is **removed**;
the per-line `execution_id` replaces it (a marker is just the first record of an
execution).

## Architecture — engine-neutral IR + swappable backend

```
   --query str ──parse(pyparsing)──▶ LogQuery (semantic IR) ──▶ JobLogBackend
                                                                  │
                                              ┌───────────────────┴────────────┐
                                              ▼                                 ▼
                                      JsonlLogBackend                  DuckDbLogBackend
                                      .record(...)                     (future: same JSONL
                                      .search(job_id, q)                file, LogQuery→SQL)
```

### `LogQuery` — semantic IR (the contract)

```python
class Op(Enum): EQ; NE; LIKE          # LIKE = substring intent, NOT a SQL pattern

@dataclass
class Predicate:
    key: str            # json key; "@timestamp"->timestamp, "@message"->whole record
    op: Op
    value: str

@dataclass
class LogQuery:
    fields: Optional[list[str]] = None        # None = all fields
    filters: list[Predicate] = field(default_factory=list)
    joiners: list[str] = field(default_factory=list)   # "and"/"or" between filters, left-to-right
    sort_key: str = "timestamp"
    sort_desc: bool = False
    limit: Optional[int] = None
```

**Critical rule:** the IR encodes *intent*, not *dialect*. `Op.LIKE` means
"substring", never a raw `%x%` or `/regex/`. Each backend renders intent into its
own syntax. Leaking SQL/Insights syntax into the IR is what breaks future
translation — see Watchouts.

### `JobLogBackend` — the swappable seam

```python
class JobLogBackend(ABC):
    @abstractmethod
    def record(self, log_path: Path, fields: dict, text: str, level: str = "info") -> None: ...
    @abstractmethod
    def search(self, log_path: Path, query: LogQuery) -> Iterator[dict]: ...
```

Injected via constructor (DI). Default-constructed at the CLI/executor edge.
Adding DuckDB later = new class implementing this ABC, reading the same JSONL
file; nothing else changes (open-closed).

### `JsonlLogBackend` — only implementation now

- `record()` — append `json.dumps({**fields, "timestamp": now, "level": level, "message": text})`.
- `search()` — `json.loads` per line, drop unparseable→`{"message": raw}`, apply
  `filters` (with `and`/`or`, `LIKE`=substring `in`), then `sort`, then `limit`.
  ~30 lines of stdlib list ops. Returns dicts (projection happens at render).

## Query language — CloudWatch Insights surface

Parsed with `pyparsing` (declarative grammar, no hand-rolled string splitting).

```
mini job logs job-ab12 --query '
  fields @timestamp, message
  | filter type = "task" and execution_id like "task-9f"
  | sort @timestamp desc
  | limit 20'
```

| verb | form | notes |
|---|---|---|
| `fields` | `fields a, b, @message` | projection; only these print, in order. Omit → all |
| `filter` | `f = "v" [and\|or g != "w"] [h like "sub"]` | `=`,`!=`,`like`(substring); `and`/`or` left-to-right, no parens |
| `sort` | `sort @timestamp asc\|desc` | one key; default `timestamp asc` |
| `limit` | `limit N` | applied after sort |

`@timestamp`→`timestamp`, `@message`→whole JSON object. Verbs optional; applied
filter→sort→limit→fields regardless of written order. Unknown verb / bad syntax
→ clear error, exit 1.

## Write side wiring

- `harness.run(..., log_path, log_fields: Optional[dict] = None)`: the harness
  stays ignorant of `Execution`. It is constructed with a `JobLogBackend`
  (injected, default `JsonlLogBackend`) and calls `backend.record(log_path,
  log_fields, text, level)` per chunk. `log_fields` default `None` keeps non-job
  callers (e.g. `PlanReviewer`) writing nothing — unchanged.
- `task_executor` owns identity: passes
  `log_fields = {"execution_id": ex.execution_id, "type": ex.execution_type}`,
  and the per-job `log_path`. Drops the `---` banner write.

## Read / render side (`cli/job.py` `job_logs`)

- no `--query` → unchanged: print every line (back-compat path).
- `--query '...'` → parse → `LogQuery` → `backend.search()` → render projecting
  exactly `fields` (rich, one column per field; `@message`=whole object).
- `--json` → emit matching records as raw JSONL (post filter/sort/limit, pre
  `fields` projection) for piping to `jq`.
- `-f/--follow` → tail JSONL file. With `--query`, the **filter** applies per new
  line; `sort`/`limit` are meaningless on a live stream → ignored with a one-line
  notice. Existing stop-on-not-RUNNING logic unchanged.

## CloudWatch ⇄ DuckDB translation watchouts

For the 4 scoped verbs the mapping is ~1:1 (`filter→WHERE`, `sort→ORDER BY`,
`limit→LIMIT`, `fields→SELECT`), so a future `DuckDbLogBackend.search()` compiles
`LogQuery`→SQL trivially and reads the same JSONL via `read_json_auto`. Divergence
only appears if scope grows:

- **`like`**: Insights `like` = substring (and `/regex/`); SQL needs `%x%` / `~`.
  Safe *because* the IR stores substring intent (`Op.LIKE`), not a pattern.
- **`stats`/aggregation** (punted): CloudWatch's grouping ≠ SQL `GROUP BY` exactly.
  Adding it needs a real aggregation IR node — this is the seam that stretches.
- **`parse`** (punted): Insights `parse` vs DuckDB `regexp_extract`/json funcs differ.

Surface-syntax choice (Insights) and engine (JSONL/DuckDB/CloudWatch) are
independent, joined only by `LogQuery`. Insights is the smaller language; anything
in it maps to SQL, not vice-versa — so committing the *user surface* to Insights
keeps any future engine able to satisfy it. A `--sql` surface could be added later
against the same backend without re-architecting.

## Testing strategy (TDD)

- `test_logquery.py`: parse Insights strings → expected `LogQuery`; bad syntax
  errors. Apply `LogQuery` over canned records: filter (`=`/`!=`/`like`, `and`/`or`),
  sort asc/desc, limit, field projection, `@timestamp`/`@message` specials,
  non-JSON line tolerated.
- `test_harness.py`: each chunk written as a JSON line with merged `log_fields`
  + `timestamp`/`level`/`message`; `log_fields=None` writes nothing (unchanged).
- `test_cli.py`: `mini job logs` no-query unchanged; `--query` filters/sorts/limits/
  projects; `--json` raw passthrough; `-f` applies filter live; "no logs yet".
- Full suite green (`pytest tests/ -q`) after each task.

## Files touched

- `src/minimise/logging/` (new): `log_query.py` (IR + pyparsing parser),
  `backend.py` (`JobLogBackend` ABC + `JsonlLogBackend`).
- `src/minimise/agents/harness.py`: write via backend, accept `log_fields`.
- `src/minimise/orchestration/task_executor.py`: pass `log_fields`, drop banner.
- `src/minimise/interfaces/cli/job.py`: `--query`/`--json` on `job_logs`, render.
