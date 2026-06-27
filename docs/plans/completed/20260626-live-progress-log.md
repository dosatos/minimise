# Live Progress Log per Job

## Overview

Today `mini`'s harness runs `claude -p --output-format text` via
`subprocess.run(..., capture_output=True)` (`agents/harness.py:85`) — a BLOCKING
call that returns the agent's entire output only at the END, or on the 900s
timeout kill. During a task the user is blind: no way to see what the agent is
doing in real time. This was the pain behind the 2026-06-25 "stuck 15 min doing
nothing" scare — the only way to learn the attempt was mid-pytest (not hung) was
to read the raw claude JSONL transcript after the fact.

Have the orchestrator (Python, not the agent) stream
the agent's stdout, extract the assistant text, and tee it to a per-job
append-only progress log that the user can tail live with
`mini job logs <job-id> -f`.

## Context

- Impacted components (in dependency order):
  - `src/minimise/agents/harness.py` — switch to `stream-json` + `subprocess.Popen`,
    read stdout line-by-line, json-parse each event, keep only `assistant` text
    blocks. Still accumulate and return the full `HarnessResult.output` for the
    EXISTING handoff/diff path — just ALSO tee each extracted line to an optional
    log sink.
  - `src/minimise/storage/job_store.py` — add a `job_log_path(job_id)` helper
    mirroring the existing `handoff_path(...)` (writes under `jobs_dir/<job>/`).
  - `src/minimise/orchestration/task_executor.py` — pass the per-job log path to
    `harness.run(...)` and write section markers per task/attempt.
  - `src/minimise/interfaces/cli/job.py` — REFOCUS the EXISTING `mini job logs`
    verb onto `job.log`: replace its current DB-derived per-task/per-attempt dump
    with reading the narration file, and add `-f/--follow` to tail it live.
- Adopted from roadmap item `feat-7-live-progress-log`.

## Relationship to `mini job status` (avoid two features for one problem)

- `mini job status` already owns the TIMELINE: per-attempt status, duration,
  expected duration, the Gantt bar, and PENDING rows (`terminal_ui.py`
  `render_execution_table_with_gantt`). `job logs` must NOT duplicate this.
- After this change the split is clean, one source each:
  - `mini job status` → timeline/progress (DB executions). Unchanged.
  - `mini job logs <id>` → the agent NARRATION, read from `job.log`. Drop the
    current per-task status/retries/duration printing (that is `status`'s job)
    and the diff-path/handoff-summary printing (deemed non-critical).
  - `mini job logs <id> -f` → tail `job.log` live.
- `Task.output` (the DB narration blob) is LEFT AS-IS: still written, still read
  by `job show --task-id`, `job results`, and the JSON/REST task endpoint. Only
  `job logs` stops reading it. Removing the column entirely is deferred to roadmap
  item `arch-3-narration-blobs-in-db`.

## Constraints (non-goals — do NOT build these)

- No DB table for log lines — a flat per-job file is enough.
- No web dashboard — `mini job logs -f` is the CLI-native equivalent of a
  streaming server.
- No new dependency — stdlib `subprocess.Popen` + `json` + `datetime` only.
- Not a structured-event store — extract assistant text for humans; the raw JSONL
  transcript already exists if structured data is ever needed.
- Do not change the `HarnessResult` return contract: `output` must still be the
  full accumulated agent text so the handoff/diff path is unaffected.

## Development Approach

- TDD: write a failing test first for each behavior, then implement.
- The harness must NOT require a real `claude` subprocess in tests. Tests inject a
  fake stream (e.g. monkeypatch `subprocess.Popen` to yield canned stream-json
  lines) — mirror how `test_harness.py` already fakes the subprocess.
- The log sink is OPTIONAL on `harness.run(...)`: when omitted (e.g. the
  `PlanReviewer` caller), behavior is unchanged and nothing is written.
- Keep the abstract `AgentHarness.run` signature backward-compatible: add the new
  parameter with a default of `None`.
- Complete each task fully (tests green) before the next. Update this plan if
  scope changes.

## Testing Strategy

- Unit tests for: stream-json parsing extracts only assistant text; full output
  is still returned intact; lines are teed to the log file with timestamps;
  `job_log_path` returns a stable per-job path; `mini job logs -f` tails the
  file.
- Run the full suite (`pytest tests/ -q`) after each task — all tests must pass
  (currently 293) before completion.

## Technical Details

- stream-json event shape: each
  stdout line is a JSON object; keep only objects where `type == "assistant"`,
  and from those concatenate the `text` fields of `message.content` blocks whose
  `type == "text"`. Ignore tool_use / system / result events for the human log
  (but they still count as the agent being alive).
- `--output-format stream-json` requires `--verbose` on the `claude` CLI; add both.
- Accumulate every extracted assistant text chunk into a buffer; that buffer is
  the returned `HarnessResult.output` (replacing the old `result.stdout`).
- Log sink: accept an optional file path on `run(...)`. Open in append mode
  (`O_APPEND` semantics) so concurrent reads see a growing file. Timestamp each
  written line Python-side with `datetime.now()`.
- `job_log_path(job_id)` writes to `jobs_dir/<job_id>/job.log` (one file
  per job). Reuse `ensure_directory` like
  the other artifact helpers in `job_store.py`.
- Section markers: `task_executor` writes a header line to the log at the start of
  each attempt, e.g. `--- task <id> attempt <n> @ <ts> ---`, before invoking the
  harness — so the tail is readable across retries.
- `mini job logs -f`: when `--follow` is set and the `job.log` file exists, stream
  new lines to the console (poll-and-print loop on the file; stop on Ctrl-C). When
  not set, keep the EXISTING DB-derived summary output unchanged.

## Implementation Steps

### Task 1: Stream-json harness with optional log sink

- [x] Write failing tests in `tests/test_harness.py`: (a) given canned
      stream-json stdout lines mixing `assistant`/`tool_use`/`result` events,
      `run(...)` returns `HarnessResult.output` equal to the concatenated
      assistant text only; (b) when a `log_path` is passed, each assistant chunk
      is appended (timestamped) to that file; (c) when `log_path` is omitted,
      no file is written and behavior matches today. Fake `subprocess.Popen`
      so no real `claude` is spawned.
- [x] Add an optional `log_path: Optional[Path] = None` (or equivalent sink)
      param to `AgentHarness.run` and `ClaudeCodeHarness.run`, keeping the
      default a no-op for existing callers.
- [x] Implement `ClaudeCodeHarness.run` with `--output-format stream-json
      --verbose`, `subprocess.Popen`, line-by-line stdout reading, JSON parse,
      assistant-text extraction, accumulate into
      the returned output, and tee to `log_path` when present. Preserve the
      existing timeout, env, returncode/error handling, and the `TimeoutExpired`
      / generic-exception fallbacks.
- [x] Run project tests — must pass before next task.

### Task 2: Per-job job-log path + wire task_executor

- [x] Write failing tests: `JobStore.job_log_path(job_id)` returns a stable
      `jobs_dir/<job_id>/job.log` path (and creates the parent dir); and
      `TaskExecutor` passes that path to `harness.run(...)` and writes a section
      marker per attempt.
- [x] Add `job_log_path(job_id)` to `job_store.py` mirroring `handoff_path`.
- [x] In `task_executor.py`, resolve the per-job job-log path, write an
      attempt-header marker to it before each `harness.run(...)` call, and pass
      the path through `_invoke_claude_code` → `harness.run(...)`.
- [x] Run project tests — must pass before next task.

### Task 3: Refocus `mini job logs` on `job.log` (replace, not extend)

This task REPLACES the body of the existing `job_logs` command — it does not add
to it. The current per-task status/retries/duration/diff dump comes out (that is
`mini job status`'s job); reading `job.log` goes in.

- [x] Write failing CLI tests (`tests/test_cli.py`): (a) `mini job logs <id>`
      prints the contents of the job's `job.log` (the agent narration), and NO
      LONGER prints the per-attempt status/duration table or diff path; (b)
      `mini job logs <id> -f` tails `job.log` live (existing content first, then
      appended lines); (c) when no `job.log` exists yet (e.g. job never started),
      it prints a clear "no logs yet" message rather than erroring.
- [x] Replace the `job_logs` command body: read the per-job `job.log` path
      (`_cli.JOBS_DIR / job_id / "job.log"`, mirroring `job show`) and print it.
      Remove the DB-derived per-task loop (status, retries, per-attempt
      executions, diff path) — that information lives in `mini job status`. Do
      NOT touch `Task.output` writes or the other readers (`job show`,
      `job results`, JSON/API) — see arch-3 deferral.
- [x] Add a `-f/--follow` flag: when set and `job.log` exists, print existing
      content then poll for appended lines; exit cleanly on Ctrl-C / when the job
      is no longer RUNNING.
- [x] Run project tests — must pass before next task.

### Task 4: Verify acceptance criteria

- [x] Verify all Overview requirements: live assistant text is visible via
      `mini job logs -f` during a running job; full output is still returned for
      the handoff/diff path; no new dependency; one log file per job.
- [x] Run the full project test suite — all tests must pass (300 passed).
- [x] Run project linter — no linter configured; pytest is the project gate per
      CLAUDE.md (skipped — still none).
