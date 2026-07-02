# exit_reason Telemetry + Retire `output` Column — Implementation Plan

> **For agentic workers:** This plan executes via the repo's `mini job`
> dogfood workflow — each task below becomes a task in a `mini` plan YAML,
> run through the `/mini-plan-review` (pre) and `/mini-implementation-review`
> (post) gates. Each task is independently committable and leaves the suite
> green. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a structured `exit_reason` per execution and make `job.log`
the single narration store by dropping the free-text `output` column.

**Architecture:** Additive first (Task 1–2 add `exit_reason`, column stays),
subtractive second (Task 3 retires `output`). This maps to roadmap feat-6
(Task 1–2) and arch-7 (Task 3), and keeps every task green.

**Tech Stack:** Python 3.9+, SQLite (stdlib `sqlite3`), Rich (tables), click.

## Global Constraints

- Tests always run with `PYTHONPATH=src pytest tests/ -q`. Baseline: 363 pass.
- No new dependencies.
- No co-author / "Generated with" trailers on commits.
- `exit_reason` vocabulary is a closed set of plain strings (no enum):
  `"success"`, `"timeout"`, `"agent_error"`, `"hook_failed"`. Blank/NULL for
  legacy rows and hook executions.
- `duration_sec` is NOT stored — it is derived from `started_at`/`completed_at`
  (the status table already does this). Do not add a duration column.

---

### Task 1: Harness sets `exit_reason`

**Files:**
- Modify: `src/minimise/agents/harness.py` (`HarnessResult`; `ClaudeCodeHarness.run` returns at lines ~166, ~180, ~182, ~188)
- Test: `tests/test_harness.py` (or the existing harness test module)

**Interfaces:**
- Produces: `HarnessResult.exit_reason: str` (default `""`). Values from the
  closed set — harness only ever sets `"success"`, `"timeout"`, `"agent_error"`.

**Context (verified):** `ClaudeCodeHarness.run` already branches at exactly the
points that know the reason: timeout-kill (`reader.is_alive()` → return at
~166), `returncode == 0` (~180), nonzero exit (~182), outer `except` (~188).
The `error` free-text field stays (it carries stderr / `"timeout after 900s"`);
`exit_reason` is the classification.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_harness.py — add three cases using the same fake-subprocess
# scaffolding the module already uses (a stub that yields stream-json lines
# and a returncode). If the module stubs Popen, reuse that stub.

def test_exit_reason_success():
    res = _run_harness_with(returncode=0, lines=['{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}'])
    assert res.success and res.exit_reason == "success"

def test_exit_reason_agent_error():
    res = _run_harness_with(returncode=1, stderr="boom")
    assert not res.success and res.exit_reason == "agent_error"

def test_exit_reason_timeout():
    # a stub whose stdout never closes, run with a tiny timeout, is killed
    res = _run_harness_timeout(timeout=0.1)
    assert not res.success and res.exit_reason == "timeout"
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_harness.py -k exit_reason -q`
Expected: FAIL — `HarnessResult` has no `exit_reason`.

- [ ] **Step 3: Implement**

```python
# HarnessResult dataclass — add field:
    exit_reason: str = ""

# In ClaudeCodeHarness.run, set it at each return:
#   timeout branch (~166):
        return HarnessResult(success=False, output="".join(chunks),
                             error=f"timeout after {timeout}s", exit_reason="timeout")
#   success (~180):
            return HarnessResult(success=True, output=output, exit_reason="success")
#   nonzero exit (~182):
        return HarnessResult(success=False, output=output,
                             error=stderr or "", exit_reason="agent_error")
#   outer except (~188):
            return HarnessResult(success=False, output="", error=str(e),
                                 exit_reason="agent_error")
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `PYTHONPATH=src pytest tests/ -q`
Expected: PASS (363 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/minimise/agents/harness.py tests/test_harness.py
git commit -m "feat: harness sets structured exit_reason (success/timeout/agent_error)"
```

---

### Task 2: Thread `exit_reason` onto the Execution row + surface it (feat-6)

**Files:**
- Modify: `src/minimise/models.py` (`Execution` ~57-101)
- Modify: `src/minimise/storage/database.py` (executions CREATE ~243, guarded ALTER ~261, `_row_to_execution` ~57-72, `save_execution` ~411-419, `SCHEMA_VERSION` ~133)
- Modify: `src/minimise/storage/job_store.py` (`record_attempt` ~103, `record_completed` ~111, `mark_task_failed` ~124, `_close_attempt` ~131)
- Modify: `src/minimise/orchestration/task_executor.py` (`_invoke_claude_code` ~137-194 return; `execute_task` failure/success calls)
- Modify: `src/minimise/interfaces/terminal_ui.py` (`render_execution_table_with_gantt` ~270; `build_steps`)
- Modify: `src/minimise/interfaces/cli/job.py` (`job_status` JSON ~223, `job_show` ~518)
- Test: `tests/test_database.py`, `tests/test_task_executor.py`, `tests/test_terminal_ui.py` (or wherever the gantt table is tested)

**Interfaces:**
- Consumes: `HarnessResult.exit_reason` (Task 1).
- Produces:
  - `Execution.exit_reason: Optional[str] = None`
  - `_invoke_claude_code(context) -> tuple[bool, str, str]` (success, output, exit_reason)
  - `JobStore.record_attempt(task, attempt, output, exit_reason="")`,
    `record_completed(task, output, diff, commit_sha=None, exit_reason="success")`,
    `mark_task_failed(task, output, exit_reason="")`
  - `render_execution_table_with_gantt` table gains a "Reason" column (col index 6, after "Type").

**Context (verified):** The executions table is created at database.py:243 and
migrated additively at :261-263 (the `hook_name` precedent — copy it). The
`output` column is NOT touched in this task; it stays and is still written.
This task is purely additive so the suite stays green throughout.

- [ ] **Step 1: Failing test — DB round-trips exit_reason**

```python
# tests/test_database.py
def test_execution_exit_reason_roundtrip(db):
    from minimise.models import Execution, TaskStatus
    ex = Execution(task_id="t1", attempt=0, job_id="j1", status=TaskStatus.FAILED,
                   exit_reason="timeout")
    db.save_execution(ex)
    got = db.list_executions_for_task("t1")[0]
    assert got.exit_reason == "timeout"
```

- [ ] **Step 2: Failing test — executor threads exit_reason to the row**

```python
# tests/test_task_executor.py — a failing attempt records exit_reason.
# Use the existing FakeHarness pattern; set exit_reason on its HarnessResult.
def test_failed_task_records_exit_reason(temp_db_dir, db, git_repo):
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=False, output="", error="timeout after 900s", exit_reason="timeout")
    executor = TaskExecutor(JobStore(db, temp_db_dir), GitTracker(git_repo), harness=fake)
    # ... create job+task as sibling tests do ...
    executor.execute_task(task, job_id, "")
    execs = db.list_executions_for_task(task.id)
    assert execs[-1].exit_reason == "timeout"
```

- [ ] **Step 3: Run to verify both fail**

Run: `PYTHONPATH=src pytest tests/test_database.py::test_execution_exit_reason_roundtrip tests/test_task_executor.py::test_failed_task_records_exit_reason -q`
Expected: FAIL (`exit_reason` not accepted / not persisted).

- [ ] **Step 4: Model + DB**

```python
# models.py Execution: add field after hook_name
    exit_reason: Optional[str] = None
# and in to_dict:
            "exit_reason": self.exit_reason,

# database.py: bump SCHEMA_VERSION = 3
# executions CREATE — add column:
                exit_reason TEXT,
# guarded migration (copy the hook_name pattern at ~261):
        if 'exit_reason' not in _column_names(cursor, "executions"):
            cursor.execute("ALTER TABLE executions ADD COLUMN exit_reason TEXT")
# _row_to_execution: exit_reason=row['exit_reason'] if 'exit_reason' in row.keys() else None
# save_execution INSERT: add exit_reason to the column list AND the values tuple.
```

- [ ] **Step 5: Store threads it through**

```python
# job_store.py — add exit_reason params, pass into the Execution via _close_attempt:
    def record_attempt(self, task, attempt, output, exit_reason=""):
        with self._close_attempt(task, attempt, TaskStatus.FAILED, output=output,
                                 exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.PENDING,
                                       output=f"Attempt {attempt} failed: {output}", conn=conn)

    def record_completed(self, task, output, diff, commit_sha=None, exit_reason="success"):
        if task.base_commit:
            self._save_diff(task, diff)
        with self._close_attempt(task, task.retries, TaskStatus.COMPLETED, output=output,
                                 diff_path=task.diff_path, commit_sha=commit_sha,
                                 exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.COMPLETED, output=output,
                                       retries=task.retries, completed_at=datetime.utcnow(), conn=conn)

    def mark_task_failed(self, task, output, exit_reason=""):
        with self._close_attempt(task, task.retries, TaskStatus.FAILED, output=output,
                                 exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.FAILED, output=output,
                                       retries=task.retries, completed_at=datetime.utcnow(), conn=conn)
# _close_attempt already forwards **exec_fields into Execution(...), so exit_reason
# flows through unchanged (it is now a real Execution field).
```

- [ ] **Step 6: Executor supplies the reason**

```python
# task_executor.py _invoke_claude_code: return the reason too
        result = self.harness.run(prompt, cwd=repo_root, allow_edits=True,
                                   log_path=log_path, log_fields=log_fields)
        return result.success, (result.output if result.success else (result.error or result.output)), result.exit_reason

# execute_task: unpack the 3-tuple
            success, output, exit_reason = self._invoke_claude_code({...})
# retry path:
                self.store.record_attempt(task, attempt, output, exit_reason=exit_reason)
# post-task hook fail/exhausted paths:
                self.store.mark_task_failed(task, msg, exit_reason="hook_failed")
# final failure:
            self.store.mark_task_failed(task, final_output, exit_reason=exit_reason)
# success:
            self.store.record_completed(task, "", diff, commit_sha=commit_sha, exit_reason="success")
# NOTE the success narration is already "" from arch-3 — keep it "".
```

- [ ] **Step 7: Fix existing executor tests that mock `_invoke_claude_code`**

The mocks at `tests/test_task_executor.py` (e.g. the `mock_invoke` returning
`(False, "boom")` / `(True, "ok")` around line 434-440, and the
`_invoke_claude_code` propagation tests ~501-576) now expect a 3-tuple. Update
each `return False, "boom"` → `return False, "boom", "agent_error"` and
`return True, "ok"` → `return True, "ok", "success"`; update the propagation
assertions to unpack `success, output, reason`.

- [ ] **Step 8: Surface — Reason column + JSON + show**

```python
# terminal_ui.py render_execution_table_with_gantt:
    table.add_column("Reason", style="dim")   # after "Type"
# add_row gains exit_reason param, appended as the last cell:
    def add_row(name, status, started_at, completed_at, estimated_duration_min, is_hook,
                timeline=None, exit_reason=""):
        table.add_row(..., "hook" if is_hook else "task", exit_reason or "")
# executions path: pass ex.exit_reason or ""
# pending/placeholder + hook rows: pass "" (default)
# plan-projected path: build_steps must carry exit_reason onto each step;
#   add_row(..., exit_reason=getattr(step, "exit_reason", "") or "")

# job.py job_status JSON: add to each task_data
                task_data["exit_reason"] = <latest execution exit_reason for the task, or None>
# (fetch via db.list_executions_for_job already loaded for the table, or
#  db.list_executions_for_task(task.id)[-1].exit_reason)

# job.py job_show: after the Status line
            console.print(f"[bold]Exit Reason:[/bold] {<latest exec>.exit_reason or '—'}")
```

- [ ] **Step 9: Test the Reason column renders**

```python
# tests/test_terminal_ui.py (or wherever the gantt table is asserted)
def test_reason_column_shows_exit_reason():
    # build one FAILED execution with exit_reason="timeout", render, assert
    # "Reason" is a column header and "timeout" appears in the row.
    ...
```

- [ ] **Step 10: Run full suite**

Run: `PYTHONPATH=src pytest tests/ -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: per-execution exit_reason telemetry, surfaced in job status/show (feat-6)"
```

---

### Task 3: Retire the free-text `output` column (arch-7)

**Files:**
- Modify: `src/minimise/orchestration/task_executor.py` (log failure detail to job.log)
- Modify: `src/minimise/storage/job_store.py` (stop writing `output`)
- Modify: `src/minimise/orchestration/hook_executor.py` (remove `ex.output = output` ~46)
- Modify: `src/minimise/interfaces/cli/_shared.py` (`task_narration` ~75-90)
- Modify: `src/minimise/models.py` (drop `output` from `Task` ~31 and `Execution` ~69 + to_dicts)
- Modify: `src/minimise/storage/database.py` (drop `output` from `tasks` + `executions` via table rebuild; update mappers/create/save; `SCHEMA_VERSION` 3 → 4)
- Test: `tests/test_narration_reader.py`, `tests/test_task_executor.py`, `tests/test_database.py`

**Interfaces:**
- Consumes: `Execution.exit_reason` (Task 2); `job.log` narration channel.
- Produces: `task_narration(job_id, task) -> str` now ALWAYS reconstructs from
  `job.log` (no `task.output` short-circuit). `Task` and `Execution` no longer
  have an `output` attribute.

**Context (verified):** After Task 2, the only remaining role of `output` is
holding failure text. arch-3 already made `task_narration` fall back to
`job.log` when `output` is empty; hooks already stream their output to
`job.log` (hook_executor.py:49-59), so `ex.output = output` there is already
dead weight. The table-rebuild migration precedent lives in database.py:209-233.

- [ ] **Step 1: Failing test — failure detail reconstructs from job.log**

```python
# tests/test_task_executor.py
def test_failure_detail_written_to_job_log(temp_db_dir, db, git_repo):
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=False, output="",
                                          error="timeout after 900s", exit_reason="timeout")
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, GitTracker(git_repo), harness=fake)
    # ... create job+task ...
    executor.execute_task(task, job_id, "")
    from minimise.interfaces.cli import _shared
    # point _shared at this job's dir, then:
    narration = _shared.task_narration(job_id, task)
    assert "timeout after 900s" in narration
```

- [ ] **Step 2: Failing test — task_narration ignores any stored output**

```python
# tests/test_narration_reader.py — REPLACE test_output_takes_precedence_and_skips_log.
def test_narration_always_from_log(tmp_path, monkeypatch):
    # write job.log with two "alpha" records; a task whose .output is set to junk
    # should STILL return the reconstructed log text (output attr is gone/ignored).
    ...
    assert task_narration(job_id, _task("Alpha")) == "alpha line 1\nalpha line 2"
```

- [ ] **Step 3: Run to verify fail**

Run: `PYTHONPATH=src pytest tests/test_task_executor.py::test_failure_detail_written_to_job_log tests/test_narration_reader.py -q`
Expected: FAIL.

- [ ] **Step 4: Executor logs failure detail to job.log**

```python
# task_executor.py — on each failure, before calling the store, append the
# terminal error string to job.log via the same backend the harness uses.
# The harness owns the backend; expose a tiny helper OR reuse JsonlLogBackend
# directly (it is already imported in harness). Simplest: give ClaudeCodeHarness
# a module-level JsonlLogBackend and call it here. Concretely, add near the top
# of execute_task after job_log_path is computed:
        from minimise.logging.backend import JsonlLogBackend
        log_backend = JsonlLogBackend()

        def _log_failure(step_label, detail):
            if detail:
                log_backend.record(str(job_log_path),
                                   {"type": "task", "step": step_label}, detail, level="error")

# then at each failure site, compute step_label the SAME way the run's log_fields
# do (task.name + "  · try N" for attempt>0) and log `output`/`combined`/`final_output`
# before the store call. Reuse the step string already built for log_fields.
```

- [ ] **Step 5: Store stops writing `output`**

```python
# job_store.py — drop the output free-text writes:
    def record_attempt(self, task, attempt, output, exit_reason=""):
        with self._close_attempt(task, attempt, TaskStatus.FAILED, exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.PENDING, conn=conn)  # no output=

    def record_completed(self, task, output, diff, commit_sha=None, exit_reason="success"):
        if task.base_commit:
            self._save_diff(task, diff)
        with self._close_attempt(task, task.retries, TaskStatus.COMPLETED,
                                 diff_path=task.diff_path, commit_sha=commit_sha,
                                 exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.COMPLETED, retries=task.retries,
                                       completed_at=datetime.utcnow(), conn=conn)

    def mark_task_failed(self, task, output, exit_reason=""):
        with self._close_attempt(task, task.retries, TaskStatus.FAILED, exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.FAILED, retries=task.retries,
                                       completed_at=datetime.utcnow(), conn=conn)
# _close_attempt no longer receives output=; drop `output` from exec_fields usage.
# Keep the `output` PARAM on the public methods (executor still passes it, now
# used only to log to job.log in Task 4/Step 4) OR drop it — see Step 8 note.
```

- [ ] **Step 6: hook_executor — remove dead output write**

```python
# hook_executor.py: delete line `ex.output = output`  (~46). Output already
# streams to job.log at lines 49-59.
```

- [ ] **Step 7: task_narration always reconstructs**

```python
# _shared.py task_narration: delete the short-circuit
#     if task.output:
#         return task.output
# so it always reads job.log. Keep the try/except → "" fallback and the
# step-base split on "  · try".
```

- [ ] **Step 8: Model — drop `output`; DB — drop columns + migration**

```python
# models.py: remove `output: Optional[str] = None` from Task (~31) and
# Execution (~69), and the "output": self.output lines from both to_dicts.

# database.py:
#  - SCHEMA_VERSION = 4
#  - tasks CREATE and executions CREATE: remove the `output TEXT,` line.
#  - _row_to_task / _row_to_execution: remove output=row['output'].
#  - create_task INSERT (~329): remove output from column list + values.
#  - save_task/update (~375, ~385): remove output.
#  - save_execution INSERT (~413): remove output from column list + values.
#  - update_task_status: remove the `output` kwarg handling (~346-347).
#  - Migration: two table rebuilds (tasks, executions) that recreate each table
#    WITHOUT `output`, guarded on the column still being present. Copy the
#    SAVEPOINT + CREATE _new + INSERT (SELECT shared cols) + DROP + RENAME
#    precedent at lines 209-233. Rebuild `executions` and `tasks` independently;
#    derive the shared column list from the NEW table via PRAGMA table_info so
#    columns stay aligned and `output` is excluded.
```

Migration sketch (per table):
```python
if 'output' in _column_names(cursor, "executions"):
    cursor.execute("SAVEPOINT drop_output_exec")
    try:
        cursor.execute("""CREATE TABLE executions_new (
            execution_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, task_id TEXT,
            execution_type TEXT NOT NULL DEFAULT 'task', attempt INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, started_at TEXT, completed_at TEXT,
            diff_path TEXT, commit_sha TEXT, hook_name TEXT, exit_reason TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id), FOREIGN KEY(task_id) REFERENCES tasks(id))""")
        cursor.execute("PRAGMA table_info(executions_new)")
        cols = ", ".join(c[1] for c in cursor.fetchall())
        cursor.execute(f"INSERT INTO executions_new ({cols}) SELECT {cols} FROM executions")
        cursor.execute("DROP TABLE executions")
        cursor.execute("ALTER TABLE executions_new RENAME TO executions")
        cursor.execute("RELEASE SAVEPOINT drop_output_exec")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT drop_output_exec"); raise
# same shape for tasks (recreate without output).
```

- [ ] **Step 9: Update remaining tests that read `.output`**

`tests/test_task_executor.py:448` asserts `"boom" in execs[0].output` — change
to `execs[0].exit_reason == "agent_error"` (the failure detail now lives in
job.log, not the row). Fix any `test_database.py` cases that pass/read
`output=` on tasks/executions. The `_task(name, output=...)` helper in
`test_narration_reader.py` can drop its `output` kwarg.

- [ ] **Step 10: Run full suite**

Run: `PYTHONPATH=src pytest tests/ -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: retire free-text output column; job.log is the sole narration store (arch-7)"
```

---

## Self-Review

**Spec coverage:**
- exit_reason vocabulary + harness source → Task 1. ✓
- Execution model/DB field, store threading, executor → Task 2. ✓
- Reason column in status, JSON, show → Task 2 Step 8. ✓
- Route failure detail to job.log → Task 3 Step 4. ✓
- task_narration simplification → Task 3 Step 7. ✓
- Drop `output` from both tables (rebuild) → Task 3 Step 8. ✓
- hook_executor dead write → Task 3 Step 6. ✓
- REST contract change (task.to_dict loses output) → falls out of model change
  in Task 3 Step 8; no separate code. ✓ (documented in spec Non-goals/§7)
- duration_sec NOT stored → Global Constraints. ✓

**Type consistency:** `exit_reason` is `str` on `HarnessResult` (default `""`),
`Optional[str]` on `Execution` (default `None`); store methods accept
`exit_reason: str = ""`. `_invoke_claude_code` returns a 3-tuple consistently
across executor + tests. `add_row` gains `exit_reason=""` used in all paths.

**Placeholder scan:** Test bodies marked `...` are scaffolding-dependent (they
reuse each module's existing fake-subprocess / temp-db fixtures); the assertions
and the change under test are concrete. Acceptable — the exact fixture wiring
mirrors sibling tests in the same file.
