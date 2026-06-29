# Named, Timed Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a hook a first-class named, estimated STEP that runs in the target project's own environment, renders on the Gantt by name, and lands in the queryable job log — replacing today's four anonymous bash-string hook fields.

**Architecture:** A hook becomes a `Hook` dataclass (`name`, `estimated_duration_min`, optional `command`). Plan/PlanTask carry two lists — `pre_hooks` / `post_hooks` — in place of the four single-string fields. One `HookExecutor.run(hook, …)` runs every hook (plan- and task-level) in the target repo's venv, records one `Execution` row, and streams output through the existing `JsonlLogBackend`. The Gantt renders a `Step` view-model assembled from the cached plan ⋈ executions, iterated in plan order.

**Tech Stack:** Python 3.9+, pydantic v2, SQLite (stdlib `sqlite3`), `rich` (terminal tables), pytest.

## Global Constraints

- Python 3.9+ — no 3.10+ syntax (`match`, `X | Y` type unions in annotations). Use `Optional[...]`, `list[...]` is fine.
- No new external dependencies. pydantic v2, `rich`, and stdlib only.
- **Run tests with `PYTHONPATH=src pytest tests/ -q`** — a stale global editable install otherwise shadows `src/`.
- `execution_id` is opaque (never parsed) but **deterministic** — it is the executions table PK and drives latest-wins resume. A re-run MUST reproduce the same id. Never make it random.
- Pre-customer: the `executions` table may be recreated, **no backfill / migration of old rows**. The `tasks`/`jobs` migration ladder in `init_db` stays untouched.
- No co-author / "Generated with" trailers on commits.
- Match-key for Step↔plan lookup is the columns `(execution_type, task_id, hook_name)` — never parse `execution_id`.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/minimise/models.py` | `Hook` dataclass; `Execution.hook_name` + new `execution_id`; `Plan`/`PlanTask` hook lists | Modify |
| `src/minimise/utils.py` | `run_shell_command(env=…)`; `project_env(repo_root)` venv helper | Modify |
| `src/minimise/storage/database.py` | `hook_name` column in `executions`; save/load round-trip | Modify |
| `src/minimise/orchestration/hook_executor.py` | Consolidated `run(hook, …)` — project env, Execution row, log stream | Rewrite |
| `src/minimise/orchestration/task_executor.py` | Drop inline hook blocks; signature loses hook strings | Modify |
| `src/minimise/orchestration/job_executor.py` | Wire plan- + task-level hook lists through `HookExecutor` | Modify |
| `src/minimise/interfaces/terminal_ui.py` | `Step` view-model; Gantt iterates plan order | Modify |
| `src/minimise/interfaces/cli/job.py` | Pass plan to renderer; job-total estimate sums hooks | Modify |
| `examples/refactor-plan-model.yaml`, `README.md` | Migrate doc references off old string fields | Modify |

---

### Task 1: `Hook` dataclass, `Execution.hook_name`, new `execution_id`

**Files:**
- Modify: `src/minimise/models.py:56-94` (Execution), add `Hook` near top
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Hook(name: str, estimated_duration_min: int, command: Optional[str] = None)` — a **pydantic `BaseModel`** (so it can be a typed `Plan`/`PlanTask` field in Task 2; constructs and reads exactly like a dataclass).
  - `Execution.hook_name: Optional[str] = None` (new field, after `commit_sha`).
  - `Execution.execution_id` property — new `{key}#{value}` format:
    - task attempt: `job#{job_id}#task#{task_id}#attempt#{attempt}`
    - task hook: `job#{job_id}#task#{task_id}#{execution_type}_hook#{hook_name}`
    - plan hook: `job#{job_id}#{execution_type}_hook#{hook_name}`
  - `Execution.to_dict()` gains `"hook_name"`.

- [x] **Step 1: Write the failing tests**

Replace `test_execution_id_derivation` in `tests/test_models.py` and add `Hook` + format tests:

```python
def test_execution_id_format():
    """Readable {key}#{value} segments; a segment present only when meaningful."""
    from minimise.models import Execution
    attempt = Execution(task_id="task-9f", attempt=1, job_id="job-ab12")
    task_hook = Execution(task_id="task-9f", attempt=0, job_id="job-ab12",
                          execution_type="post_task", hook_name="pytest")
    plan_hook = Execution(task_id=None, attempt=0, job_id="job-ab12",
                          execution_type="post_plan", hook_name="deploy")

    assert attempt.execution_id == "job#job-ab12#task#task-9f#attempt#1"
    assert task_hook.execution_id == "job#job-ab12#task#task-9f#post_task_hook#pytest"
    assert plan_hook.execution_id == "job#job-ab12#post_plan_hook#deploy"


def test_execution_id_deterministic_and_distinct():
    from minimise.models import Execution
    a = Execution(task_id="t1", attempt=2, job_id="j1")
    assert a.execution_id == Execution(task_id="t1", attempt=2, job_id="j1").execution_id
    h1 = Execution(task_id="t1", attempt=0, job_id="j1", execution_type="post_task", hook_name="ruff")
    h2 = Execution(task_id="t1", attempt=0, job_id="j1", execution_type="post_task", hook_name="pytest")
    assert h1.execution_id != h2.execution_id  # named hooks no longer collide


def test_hook_dataclass_shape():
    from minimise.models import Hook
    script = Hook(name="Run tests", estimated_duration_min=3, command="pytest -q")
    ref = Hook(name="security", estimated_duration_min=5)
    assert script.command == "pytest -q"
    assert ref.command is None


def test_execution_to_dict_has_hook_name():
    from minimise.models import Execution
    d = Execution(task_id="t1", attempt=0, job_id="j1",
                  execution_type="post_task", hook_name="pytest").to_dict()
    assert d["hook_name"] == "pytest"
    assert d["execution_id"].endswith("post_task_hook#pytest")
```

Delete the old `test_execution_id_derivation` (it asserts the dropped `#type#` format).

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'Hook'` / `hook_name` unexpected keyword / old id format.

- [x] **Step 3: Add `Hook` and update `Execution`**

In `src/minimise/models.py`, after the `Plan` model (or anywhere the pydantic imports are in scope), add `Hook` as a pydantic model — it must be a `BaseModel` so Task 2 can use it as a typed `Plan`/`PlanTask` field:

```python
class Hook(BaseModel):
    """A named, timed lifecycle step. Has a command -> a shell script;
    no command -> a bare name resolved from config (deferred). No type, no when."""
    name: str
    estimated_duration_min: int = Field(gt=0, strict=True)
    command: Optional[str] = None
```

(`Field`, `BaseModel` are already imported at the top of `models.py`.)

In `Execution`, add the field (after `commit_sha`):

```python
    commit_sha: Optional[str] = None
    hook_name: Optional[str] = None  # set on hooks; NULL for task attempts
```

Replace the `execution_id` property:

```python
    @property
    def execution_id(self) -> str:
        """Deterministic opaque id. Readable {key}#{value} pairs; a segment is
        present only when meaningful. Never parse it — it's the PK / resume key."""
        parts = [f"job#{self.job_id}"]
        if self.task_id:
            parts.append(f"task#{self.task_id}")
        if self.hook_name:
            parts.append(f"{self.execution_type}_hook#{self.hook_name}")
        else:
            parts.append(f"attempt#{self.attempt}")
        return "#".join(parts)
```

Add `hook_name` to `to_dict()` (after `commit_sha`):

```python
            "commit_sha": self.commit_sha,
            "hook_name": self.hook_name,
        }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_models.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/minimise/models.py tests/test_models.py
git commit -m "feat: Hook dataclass + hook_name identity + readable execution_id"
```

---

### Task 2: Plan / PlanTask hook lists

**Files:**
- Modify: `src/minimise/models.py:124-153` (PlanTask, Plan)
- Test: `tests/test_plan_validator.py`

**Interfaces:**
- Consumes: `Hook` (Task 1).
- Produces:
  - `PlanTask.pre_hooks: list[Hook]`, `PlanTask.post_hooks: list[Hook]` (default `[]`).
  - `Plan.pre_hooks: list[Hook]`, `Plan.post_hooks: list[Hook]` (default `[]`) — replace `pre_plan_hook` / `post_plan_hook`.
  - Validation errors: duplicate hook name within one list; a hook with neither `command` nor a name-only-with-no-command-resolver (bare name → error until config-hooks ship).

`Hook` is already a pydantic `BaseModel` (Task 1), so it drops straight into `Plan`/`PlanTask` as a typed list field — no separate spec/conversion type needed.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_plan_validator.py`:

```python
def test_plan_task_hook_lists_parse():
    from minimise.models import Plan
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "setup", "command": "make init", "estimated_duration_min": 1}],
        "post_hooks": [{"name": "deploy", "command": "deploy.sh", "estimated_duration_min": 5}],
        "tasks": [{
            "id": "t1", "name": "Build", "description": "d", "goal": "g",
            "estimated_duration_min": 3,
            "post_hooks": [
                {"name": "Run tests", "command": "pytest -q", "estimated_duration_min": 3},
                {"name": "Lint", "command": "ruff check", "estimated_duration_min": 1},
            ],
        }],
    })
    assert plan.pre_hooks[0].name == "setup"
    assert plan.post_hooks[0].command == "deploy.sh"
    assert [h.name for h in plan.tasks[0].post_hooks] == ["Run tests", "Lint"]
    assert plan.tasks[0].pre_hooks == []


def test_duplicate_hook_names_rejected():
    import pytest
    from pydantic import ValidationError
    from minimise.models import Plan
    with pytest.raises(ValidationError, match="unique"):
        Plan.model_validate({
            "name": "P",
            "tasks": [{
                "id": "t1", "name": "Build", "description": "d", "goal": "g",
                "estimated_duration_min": 3,
                "post_hooks": [
                    {"name": "dup", "command": "a", "estimated_duration_min": 1},
                    {"name": "dup", "command": "b", "estimated_duration_min": 1},
                ],
            }],
        })


def test_bare_name_hook_without_command_rejected():
    import pytest
    from pydantic import ValidationError
    from minimise.models import Plan
    # A name-only hook references a config-hook; until that ships, it's an error.
    with pytest.raises(ValidationError, match="command"):
        Plan.model_validate({
            "name": "P",
            "tasks": [{
                "id": "t1", "name": "Build", "description": "d", "goal": "g",
                "estimated_duration_min": 3,
                "post_hooks": [{"name": "security", "estimated_duration_min": 5}],
            }],
        })
```

**Surgically edit `test_extras_preserved`** (lines ~94-101): this test asserts both `plan.briefing == "context here"` (keep — extras coverage) AND `plan.tasks[0].pre_task_hook == "echo hi"` (delete — the field is gone). Remove ONLY these two lines:

```python
        data["tasks"][0]["pre_task_hook"] = "echo hi"   # delete
        ...
        assert plan.tasks[0].pre_task_hook == "echo hi"  # delete
```

Leave the `briefing` setup and assertion intact. Do not delete the whole test.

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_plan_validator.py -q`
Expected: FAIL — `pre_hooks`/`post_hooks` not recognized (stored as extras, not typed lists) and no validation.

- [x] **Step 3: Implement the schema**

In `src/minimise/models.py`, update `PlanTask` and `Plan` to use the `Hook` model. Add a shared validator helper module-level:

```python
def _validate_hook_list(hooks: list["Hook"], where: str) -> None:
    names = [h.name for h in hooks]
    if len(names) != len(set(names)):
        raise ValueError(f"hook names must be unique within {where}")
    for h in hooks:
        if h.command is None:
            # Bare name = a config-hook reference; resolver not built yet.
            raise ValueError(
                f"hook '{h.name}' in {where} has no command "
                "(named config-hooks are not supported yet)"
            )
```

`PlanTask`:

```python
class PlanTask(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    description: str
    goal: str
    estimated_duration_min: int = Field(gt=0, strict=True)
    pre_hooks: list[Hook] = Field(default_factory=list)
    post_hooks: list[Hook] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_hooks(self):
        _validate_hook_list(self.pre_hooks, f"task '{self.id}' pre_hooks")
        _validate_hook_list(self.post_hooks, f"task '{self.id}' post_hooks")
        return self
```

`Plan` — replace `pre_plan_hook` / `post_plan_hook`:

```python
class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    tasks: list[PlanTask] = Field(min_length=1)
    pre_hooks: list[Hook] = Field(default_factory=list)
    post_hooks: list[Hook] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_task_ids(self):
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        _validate_hook_list(self.pre_hooks, "plan pre_hooks")
        _validate_hook_list(self.post_hooks, "plan post_hooks")
        return self
```

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_plan_validator.py tests/test_models.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/minimise/models.py tests/test_plan_validator.py
git commit -m "feat: plan pre_hooks/post_hooks lists replace string hook fields"
```

---

### Task 3: `hook_name` column in the executions table

**Files:**
- Modify: `src/minimise/storage/database.py:57-69` (`_row_to_execution`), `:240-256` (schema), `:400-412` (`save_execution`)
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: `Execution.hook_name` (Task 1).
- Produces: `executions.hook_name` (TEXT, NULL for task attempts); `save_execution` persists it; `_row_to_execution` reads it.

**Note:** Pre-customer — the `executions` table is recreated, no backfill. Because `init_db` guards on `PRAGMA user_version == SCHEMA_VERSION`, **bump `SCHEMA_VERSION` to 2** so existing dev DBs re-run `init_db`. The `executions` table is `CREATE TABLE IF NOT EXISTS` — add the column with an `ALTER TABLE … ADD COLUMN` guard mirroring the `tasks` migration pattern (lines 187-193), so an existing table gains the column without a drop.

- [x] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_execution_hook_name_round_trips(tmp_path):
    from minimise.storage.database import Database
    from minimise.models import Execution, Job, JobStatus, TaskStatus
    from datetime import datetime
    db = Database(tmp_path / "t.db")
    db.init_db()
    db.create_job(Job(id="j1", name="J", status=JobStatus.RUNNING,
                      created_at=datetime.utcnow()))
    ex = Execution(task_id="t1", attempt=0, job_id="j1",
                   execution_type="post_task", hook_name="pytest",
                   status=TaskStatus.FAILED, started_at=datetime.utcnow(),
                   completed_at=datetime.utcnow())
    db.save_execution(ex)
    loaded = db.list_executions_for_job("j1")
    assert len(loaded) == 1
    assert loaded[0].hook_name == "pytest"
    assert loaded[0].execution_id.endswith("post_task_hook#pytest")
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_database.py::test_execution_hook_name_round_trips -q`
Expected: FAIL — `sqlite3.OperationalError: table executions has no column named hook_name` (or `hook_name` not read back).

- [x] **Step 3: Implement the column**

In `database.py`, bump the version constant (line 131):

```python
    SCHEMA_VERSION = 2
```

In the `executions` `CREATE TABLE IF NOT EXISTS` block (after `commit_sha TEXT,`):

```python
                commit_sha TEXT,
                hook_name TEXT,
```

Immediately after that `CREATE TABLE` statement (before `PRAGMA user_version`), add the ALTER guard for existing tables:

```python
        if 'hook_name' not in _column_names(cursor, "executions"):
            cursor.execute("ALTER TABLE executions ADD COLUMN hook_name TEXT")
```

In `save_execution` (lines 403-412), add the column + value:

```python
            cursor.execute("""
                INSERT OR REPLACE INTO executions
                    (execution_id, job_id, task_id, execution_type, attempt, status,
                     started_at, completed_at, output, diff_path, commit_sha, hook_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (execution.execution_id, execution.job_id, execution.task_id,
                  execution.execution_type, execution.attempt, execution.status.value,
                  execution.started_at.isoformat() if execution.started_at else None,
                  execution.completed_at.isoformat() if execution.completed_at else None,
                  execution.output, execution.diff_path, execution.commit_sha,
                  execution.hook_name))
```

In `_row_to_execution` (lines 58-69), read it (guard for older rows):

```python
def _row_to_execution(row: sqlite3.Row) -> Execution:
    keys = row.keys()
    return Execution(
        task_id=row['task_id'],
        attempt=row['attempt'],
        job_id=row['job_id'],
        execution_type=row['execution_type'],
        status=TaskStatus(row['status']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        output=row['output'],
        diff_path=row['diff_path'],
        commit_sha=row['commit_sha'],
        hook_name=row['hook_name'] if 'hook_name' in keys else None,
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_database.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/minimise/storage/database.py tests/test_database.py
git commit -m "feat: hook_name column on executions (identity for named hooks)"
```

---

### Task 4: `run_shell_command(env=…)` + `project_env(repo_root)` venv helper

**Files:**
- Modify: `src/minimise/utils.py:12-37` (`run_shell_command`), add `project_env`
- Test: `tests/test_utils.py` (create if absent)

**Interfaces:**
- Produces:
  - `run_shell_command(command, cwd=None, timeout=3600, env=None)` — forwards `env` to `subprocess.run(env=…)`; `env=None` keeps today's inherited-environment behavior.
  - `project_env(repo_root: Path) -> Optional[dict]` — if `<repo_root>/.venv/bin` or `<repo_root>/venv/bin` exists, return `{**os.environ, "PATH": "<venv_bin>:<PATH>", "VIRTUAL_ENV": "<venv>"}`; else `None` (caller falls back to inherited PATH).

- [x] **Step 1: Write the failing tests**

Create `tests/test_utils.py`:

```python
import os
from pathlib import Path
from minimise.utils import run_shell_command, project_env


def test_run_shell_command_uses_env(tmp_path):
    ok, out = run_shell_command("echo $MINI_MARKER",
                                env={**os.environ, "MINI_MARKER": "xyz"})
    assert ok and "xyz" in out


def test_project_env_detects_dotvenv(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    env = project_env(tmp_path)
    assert env is not None
    assert env["PATH"].startswith(str(venv_bin) + os.pathsep)
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_project_env_none_when_no_venv(tmp_path):
    assert project_env(tmp_path) is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_utils.py -q`
Expected: FAIL — `run_shell_command() got unexpected keyword 'env'`; `cannot import name 'project_env'`.

- [x] **Step 3: Implement**

In `src/minimise/utils.py`, add `import os` at top, then:

```python
def run_shell_command(command: str, cwd: Optional[Path] = None, timeout: int = 3600,
                      env: Optional[dict] = None) -> tuple[bool, str]:
    """Execute a shell command; return (success, combined stdout+stderr).

    env=None inherits the current process environment (today's behavior);
    pass a dict to run with a replaced environment (e.g. the target repo's venv).
    """
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def project_env(repo_root: Path) -> Optional[dict]:
    """Environment for running a hook inside the TARGET repo's venv (fixes BUG-4).

    If the repo has a .venv/ (or venv/), prepend its bin to PATH and set
    VIRTUAL_ENV so `pytest`/`ruff`/etc. resolve to the project's, not minimise's.
    Returns None when no venv exists -> caller runs with inherited PATH.
    """
    repo_root = Path(repo_root)
    for name in (".venv", "venv"):
        venv = repo_root / name
        venv_bin = venv / "bin"
        if venv_bin.exists():
            return {
                **os.environ,
                "PATH": f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "VIRTUAL_ENV": str(venv),
            }
    return None
```

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_utils.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/minimise/utils.py tests/test_utils.py
git commit -m "feat: run_shell_command env param + project_env venv detection (BUG-4)"
```

---

### Task 5: `HookExecutor.run(hook, …)` — project env, Execution row, log stream

**Files:**
- Rewrite: `src/minimise/orchestration/hook_executor.py`
- Test: `tests/test_hook_executor.py` (**already exists — OVERWRITE it**; its current tests call the old `run("exit 0", "Pre-plan")` string API and must be deleted)

**Interfaces:**
- Consumes: `Hook` (T1), `Execution.hook_name` (T1), `project_env`/`run_shell_command` (T4), `JsonlLogBackend.record` (existing).
- Produces:
  ```python
  HookExecutor(store=None, job_id=None, repo_root=None, log_path=None, backend=None)
  HookExecutor.run(hook: Hook, execution_type: str, task_id: Optional[str]) -> bool
  ```
  - `job_id` is bound on the executor (as today's `HookExecutor.job_id` is) and stamped into the recorded `Execution`.
  - `execution_type` is one of `"pre_plan"`, `"post_plan"`, `"pre_task"`, `"post_task"`.
  - Runs `hook.command` in the target repo's venv (`project_env(repo_root)`, `cwd=repo_root`); if no venv, inherited PATH.
  - Records one `Execution(execution_type, task_id, hook_name=hook.name, attempt=0, …)` via `store.save_execution` when `store` is set.
  - Streams one log line via `backend.record(log_path, {"execution_id":…, "type": execution_type}, message, level="error" if failed else "info")` when `log_path` + `backend` are set.
  - Returns `True`/`False`. (A `None` command can't reach here — rejected at parse time, Task 2.)

- [x] **Step 1: Overwrite `tests/test_hook_executor.py`**

The file exists with four old-API tests (`test_run_without_store_unchanged`, `test_records_success`, `test_records_failure`, `test_empty_command_records_nothing`) that call `run("exit 0", "Pre-plan")`. **Replace the entire file** with:

```python
import json
from pathlib import Path
from minimise.orchestration.hook_executor import HookExecutor
from minimise.models import Hook
from minimise.logging.backend import JsonlLogBackend


def test_run_success_returns_true():
    h = Hook(name="ok", command="exit 0", estimated_duration_min=1)
    assert HookExecutor().run(h, "post_task", task_id="t1") is True


def test_run_failure_returns_false():
    h = Hook(name="bad", command="exit 1", estimated_duration_min=1)
    assert HookExecutor().run(h, "post_task", task_id="t1") is False


def test_records_execution_with_hook_name(tmp_path):
    from minimise.storage.database import Database
    from minimise.models import Job, JobStatus
    from datetime import datetime
    db = Database(tmp_path / "t.db"); db.init_db()
    db.create_job(Job(id="j1", name="J", status=JobStatus.RUNNING, created_at=datetime.utcnow()))
    HookExecutor(store=db, job_id="j1").run(
        Hook(name="pytest", command="exit 1", estimated_duration_min=1),
        "post_task", task_id="t1")
    rows = db.list_executions_for_job("j1")
    assert any(r.hook_name == "pytest" and r.execution_type == "post_task" for r in rows)


def test_failed_hook_logs_error_line(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="pytest", command="exit 1", estimated_duration_min=1),
        "post_task", task_id="t1")
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    err = [r for r in recs if r["level"] == "error"]
    assert err and err[0]["type"] == "post_task"
    assert "post_task_hook#pytest" in err[0]["execution_id"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_hook_executor.py -q`
Expected: FAIL — `run()` signature mismatch (old `run(command, label)`).

- [x] **Step 3: Rewrite `hook_executor.py`**

```python
"""HookExecutor — runs a named, timed hook (plan- or task-level).

Runs the hook's command in the TARGET repo's environment (fixes BUG-4),
records one timed Execution, and streams a log line through the existing
JSONL backend so the run is queryable. Failing the job is the run loop's
concern (JobExecutor/TaskExecutor), not here.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from minimise.models import Execution, Hook, TaskStatus
from minimise.utils import project_env, run_shell_command


class HookExecutor:
    def __init__(self, store=None, job_id=None, repo_root: Optional[Path] = None,
                 log_path=None, backend=None):
        self.store = store
        self.job_id = job_id
        self.repo_root = Path(repo_root) if repo_root else None
        self.log_path = log_path
        self.backend = backend

    def run(self, hook: Hook, execution_type: str, task_id: Optional[str]) -> bool:
        """Run one hook in the project env; record + log; return success."""
        started_at = datetime.utcnow()
        env = project_env(self.repo_root) if self.repo_root else None
        success, output = run_shell_command(hook.command, cwd=self.repo_root, env=env)
        completed_at = datetime.utcnow()

        ex = Execution(
            job_id=self.job_id, task_id=task_id, execution_type=execution_type,
            attempt=0, hook_name=hook.name,
            status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
            started_at=started_at, completed_at=completed_at, output=output,
        )
        if self.store:
            self.store.save_execution(ex)
        if self.log_path and self.backend:
            self.backend.record(
                self.log_path,
                {"execution_id": ex.execution_id, "type": execution_type},
                f"{hook.name} — {'ok' if success else 'failed'}: {output.strip()[:500]}",
                level="error" if not success else "info",
            )
        if not success:
            print(f"Hook '{hook.name}' ({execution_type}) failed: {output}")
        return success


def demo():
    assert HookExecutor().run(Hook(name="ok", command="exit 0", estimated_duration_min=1), "post_task", "t1") is True
    assert HookExecutor().run(Hook(name="bad", command="exit 1", estimated_duration_min=1), "pre_plan", None) is False
    print("OK")


if __name__ == "__main__":
    demo()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_hook_executor.py -q && PYTHONPATH=src python src/minimise/orchestration/hook_executor.py`
Expected: PASS; demo prints `OK`.

- [x] **Step 5: Commit**

```bash
git add src/minimise/orchestration/hook_executor.py tests/test_hook_executor.py
git commit -m "feat: HookExecutor runs named hooks in project env, records + logs"
```

---

### Task 6: Wire task- and plan-level hooks through `HookExecutor`

**Files:**
- Modify: `src/minimise/orchestration/task_executor.py:26-58, 96-106` (drop inline hook blocks; signature)
- Modify: `src/minimise/orchestration/job_executor.py` (iterate hook lists)
- Modify: `src/minimise/orchestration/job_controller.py:32` (wire `repo_root`, `log_path`, `backend` into `HookExecutor`)
- Test: `tests/test_job_controller.py`, `tests/test_task_executor.py`, `tests/test_job_executor.py`

**Interfaces:**
- Consumes: `HookExecutor.run(hook, execution_type, task_id)` (T5), `Plan`/`PlanTask` hook lists (T2).
- Produces:
  - `TaskExecutor.execute_task(task, job_id, handover_context, next_task=None)` — **no more `pre_task_hook`/`post_task_hook` params**. The inline pre/post hook blocks are removed.
  - `JobExecutor.execute(job, plan)` runs, per task: `plan_task.pre_hooks` → task attempts → `plan_task.post_hooks`; brackets the whole run with `plan.pre_hooks` / `plan.post_hooks`. A failed `post_hook` fails the task (and the job).

**Design note — who fails the task?** Today `TaskExecutor` marks the task FAILED on a post-hook failure (`mark_task_failed`). With hooks moving to `JobExecutor`, the post-task-hook failure path must still mark the task failed. Keep it simple: `JobExecutor` calls `self.task_executor.store.mark_task_failed(task, msg)` when a post-task hook fails. The `store` is already on `TaskExecutor`.

- [x] **Step 1: Write the failing tests**

In `tests/test_task_executor.py`, the hook tests (lines ~116, 162, 565-640) now belong to `HookExecutor` (Task 5) — **delete the hook-param tests** (`test_pre_task_hook_recorded`, `test_pre_task_hook_failure_recorded`, `test_post_task_hook_recorded`, `test_task_attempt_started_at_not_copied_from_pre_task_hook`, and the hook kwargs in lines 116/162). Update remaining `execute_task(...)` calls to drop `pre_task_hook=`/`post_task_hook=`.

Add to `tests/test_job_controller.py` a hook-ordering test:

```python
def test_job_runs_task_hooks_in_plan_order(job_controller, tmp_path):
    """pre_hooks -> task -> post_hooks, recorded as executions with hook_name."""
    from minimise.models import Plan
    import yaml
    plan_dict = {
        "name": "P",
        "tasks": [{
            "id": "t1", "name": "Build", "description": "d", "goal": "g",
            "estimated_duration_min": 1,
            "pre_hooks": [{"name": "setup", "command": "true", "estimated_duration_min": 1}],
            "post_hooks": [{"name": "verify", "command": "true", "estimated_duration_min": 1}],
        }],
    }
    # (use the suite's existing job-creation fixture path; assert executions carry
    #  hook_name "setup" and "verify" with types pre_task / post_task)
```

> Adapt to the file's existing fixtures (`plan_file`, `job_controller`). The suite currently builds plan dicts with `pre_task_hook: ""` strings (lines 68-87 etc.) — **omit those four string keys** (the new `pre_hooks`/`post_hooks` default to `[]`). The `MockTaskExecutor.execute_task` / `mock_execute_task` signatures (lines 187, 233, 294, 379, 466, 511, 613) must drop the `pre_task_hook=""`/`post_task_hook=""` params, since `JobExecutor` no longer passes them.

**CRITICAL — rewrite the two plan-hook-failure tests, don't just blank the strings.** `test_pre_plan_hook_failure_persists_job` (line 541) and `test_post_plan_hook_failure_persists_job` (line 584) set `"pre_plan_hook": "exit 1"` / `"post_plan_hook": "exit 1"` to assert the job FAILS. After Task 2, those keys are silent extras (`extra="allow"`), the hook never runs, and the tests would pass while testing nothing — a false green. Rewrite each to use the list form so the failure is real:

```python
    plan_content = {
        "name": "Test Plan with Hook Failure",
        "briefing": "Plan with failing pre hook",
        "pre_hooks": [{"name": "guard", "command": "exit 1", "estimated_duration_min": 1}],
        "tasks": [{
            "id": "task-1", "name": "Task 1", "description": "First task",
            "goal": "Complete task", "estimated_duration_min": 5,
        }],
    }
```

(post-plan variant: move the failing hook to `"post_hooks"`, keep the task-succeeds mock.) Assertions (`job.status == JobStatus.FAILED`, tasks PENDING for the pre-plan case) stay as-is.

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_task_executor.py tests/test_job_controller.py tests/test_job_executor.py -q`
Expected: FAIL — signature mismatches; plan-hook-failure tests fail because the failing hook isn't wired through the new list path yet.

- [x] **Step 3: Trim `TaskExecutor`**

In `task_executor.py`, change the signature (remove hook params):

```python
    def execute_task(
        self,
        task: Task,
        job_id: str,
        handover_context: str,
        next_task: Optional[Task] = None,
    ) -> tuple[bool, str]:
```

Delete the `if pre_task_hook:` block (lines 49-58) and the `if post_task_hook:` block (lines 96-106). Remove the now-unused `run_shell_command` import if nothing else uses it (grep first — `_invoke_claude_code` does not use it).

- [x] **Step 4: Rewrite `JobExecutor.execute`**

```python
class JobExecutor:
    def __init__(self, task_executor, hook_executor, git_tracker):
        self.task_executor = task_executor
        self.hook_executor = hook_executor
        self.git_tracker = git_tracker

    def _run_hooks(self, hooks, execution_type, task_id) -> bool:
        for hook in hooks:
            if not self.hook_executor.run(hook, execution_type, task_id):
                print(f"{execution_type} hook '{hook.name}' failed")
                return False
        return True

    def execute(self, job, plan) -> bool:
        if not self._run_hooks(plan.pre_hooks, "pre_plan", None):
            return False

        handover = ""
        for idx, task in enumerate(job.tasks):
            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            next_task = job.tasks[idx + 1] if idx < len(job.tasks) - 1 else None
            pre = getattr(plan_task, "pre_hooks", []) if plan_task else []
            post = getattr(plan_task, "post_hooks", []) if plan_task else []

            if not self._run_hooks(pre, "pre_task", task.id):
                self.task_executor.store.mark_task_failed(task, "Pre-task hook failed")
                return False

            success, output = self.task_executor.execute_task(task, job.id, handover, next_task=next_task)
            if not success:
                print(f"Task {task.name} failed: {output}")
                return False

            if not self._run_hooks(post, "post_task", task.id):
                self.task_executor.store.mark_task_failed(task, "Post-task hook failed")
                return False

            handover = output

        return self._run_hooks(plan.post_hooks, "post_plan", None)
```

- [x] **Step 5: Wire `HookExecutor` in `JobController`**

In `job_controller.py:32`, replace `self.hook_executor = HookExecutor(self.db)` with the fully wired executor. Note this also changes the `store` from `self.db` to `self.store` (the `JobStore`) — both expose `save_execution`, and routing through `JobStore` is the right layer:

```python
from minimise.logging.backend import JsonlLogBackend
...
        self.hook_executor = HookExecutor(
            store=self.store, repo_root=self.repo_path, backend=JsonlLogBackend(),
        )
```

`start_job` already sets `self.hook_executor.job_id = job_id`; also set the log path there:

```python
        self.hook_executor.job_id = job_id
        self.hook_executor.log_path = self.store.job_log_path(job_id)
```

- [x] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_task_executor.py tests/test_job_controller.py tests/test_job_executor.py tests/test_hook_executor.py -q`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/minimise/orchestration/
git add tests/test_task_executor.py tests/test_job_controller.py tests/test_job_executor.py
git commit -m "feat: run task + plan hooks through HookExecutor in plan order"
```

---

### Task 7: `Step` view-model + Gantt renders in plan order

**Files:**
- Modify: `src/minimise/interfaces/terminal_ui.py:160-258`
- Modify: `src/minimise/interfaces/cli/job.py:308-318` (pass plan to renderer)
- Test: `tests/test_terminal_ui.py`

**Interfaces:**
- Consumes: `Plan` (T2), `Execution` with `hook_name` (T1/T3).
- Produces:
  - `Step` (dataclass, in `terminal_ui.py`): `name, estimate, status, started_at, ended_at`.
  - `build_steps(plan, tasks, executions) -> list[Step]` — iterate plan order (per task: `pre_hooks` → attempts → `post_hooks`; plan hooks bracket). Match each plan def to an execution row on `(execution_type, task_id, hook_name)`; task attempts match on `task_id` (+ one Step per attempt row found, else one PENDING Step). No execution row → PENDING (`status=TaskStatus.PENDING`, no timestamps).
  - `render_execution_table_with_gantt(job, tasks, plan, now=None)` — gains a `plan` param; renders `build_steps(...)`. Keeps the legacy `executions` / `executions_by_task` params working for callers that pass them (don't break `test_terminal_ui.py`'s existing legacy tests).

**Design note:** This is the largest single task. Keep `build_steps` pure (no I/O) so it tests without a DB. The renderer's `add_row` helper already exists — feed it `Step` fields.

**Do NOT delete the legacy renderer tests.** `tests/test_terminal_ui.py` has hook-label tests that call `render_execution_table_with_gantt(..., executions=[...])` with **no `plan` arg** and assert labels like `"Pre-plan hook"`, `"Post-task hook  · Build"` (around lines 557-665). Task 7 ADDS a `plan` branch but leaves the existing `executions is not None` branch (terminal_ui.py:214-236) untouched, so these stay green. Leave them as-is — they cover the legacy path until callers migrate.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_terminal_ui.py`:

```python
def test_build_steps_plan_order_with_pending_hook():
    from datetime import datetime
    from minimise.interfaces.terminal_ui import build_steps
    from minimise.models import Plan, Execution, Task, TaskStatus

    plan = Plan.model_validate({
        "name": "P",
        "tasks": [{
            "id": "t1", "name": "Build", "description": "d", "goal": "g",
            "estimated_duration_min": 3,
            "pre_hooks": [{"name": "setup", "command": "true", "estimated_duration_min": 1}],
            "post_hooks": [{"name": "pytest", "command": "pytest", "estimated_duration_min": 2}],
        }],
    })
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d",
                  estimated_duration_min=3, goal="g")]
    # only the pre-hook + the task attempt have run; post-hook is pending
    execs = [
        Execution(task_id="task-1", attempt=0, job_id="j1", execution_type="pre_task",
                  hook_name="setup", status=TaskStatus.COMPLETED,
                  started_at=datetime(2026,1,1,0,0,0), completed_at=datetime(2026,1,1,0,0,1)),
        Execution(task_id="task-1", attempt=0, job_id="j1", execution_type="task",
                  status=TaskStatus.COMPLETED,
                  started_at=datetime(2026,1,1,0,0,1), completed_at=datetime(2026,1,1,0,0,5)),
    ]
    steps = build_steps(plan, tasks, execs)
    assert [s.name for s in steps] == ["setup", "Build  · try 1", "pytest"]
    assert steps[0].status == TaskStatus.COMPLETED
    assert steps[2].status == TaskStatus.PENDING  # post-hook not run, drawn from plan
    assert steps[2].estimate == 2


def test_build_steps_brackets_plan_hooks():
    from minimise.interfaces.terminal_ui import build_steps
    from minimise.models import Plan, Task
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "init", "command": "true", "estimated_duration_min": 1}],
        "post_hooks": [{"name": "deploy", "command": "true", "estimated_duration_min": 5}],
        "tasks": [{"id": "t1", "name": "Build", "description": "d", "goal": "g",
                   "estimated_duration_min": 3}],
    })
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d",
                  estimated_duration_min=3, goal="g")]
    steps = build_steps(plan, tasks, [])
    assert [s.name for s in steps] == ["init", "Build", "deploy"]  # all PENDING, plan order
```

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_terminal_ui.py -q`
Expected: FAIL — `cannot import name 'build_steps'`.

- [x] **Step 3: Implement `Step` + `build_steps`**

In `terminal_ui.py`, add near the top (after imports):

```python
from dataclasses import dataclass
from minimise.models import Hook, Plan, Execution


@dataclass
class Step:
    """One Gantt row — a task attempt or a hook. Name/estimate from the plan,
    status/timing from the execution (PENDING when none)."""
    name: str
    estimate: Optional[int]
    status: TaskStatus
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


def _match_hook(execs, execution_type, task_id, hook_name):
    return next((e for e in execs if e.execution_type == execution_type
                 and e.task_id == task_id and e.hook_name == hook_name), None)


def _hook_steps(hooks, execs, execution_type, task_id):
    steps = []
    for hook in hooks:
        ex = _match_hook(execs, execution_type, task_id, hook.name)
        steps.append(Step(
            name=hook.name, estimate=hook.estimated_duration_min,
            status=ex.status if ex else TaskStatus.PENDING,
            started_at=ex.started_at if ex else None,
            ended_at=ex.completed_at if ex else None,
        ))
    return steps


def build_steps(plan: Plan, tasks: list, executions: list) -> list:
    """Assemble Gantt rows in plan order: plan.pre_hooks, then per task
    (pre_hooks -> attempts -> post_hooks), then plan.post_hooks."""
    steps = list(_hook_steps(plan.pre_hooks, executions, "pre_plan", None))
    names = {t.name: t for t in tasks}
    for idx, ptask in enumerate(plan.tasks):
        task = tasks[idx] if idx < len(tasks) else None
        task_id = task.id if task else None
        steps += _hook_steps(ptask.pre_hooks, executions, "pre_task", task_id)

        attempts = sorted(
            (e for e in executions if e.execution_type == "task" and e.task_id == task_id),
            key=lambda e: e.attempt,
        )
        if attempts:
            for e in attempts:
                steps.append(Step(name=f"{ptask.name}  · try {e.attempt + 1}",
                                  estimate=ptask.estimated_duration_min, status=e.status,
                                  started_at=e.started_at, ended_at=e.completed_at))
        else:
            steps.append(Step(name=ptask.name, estimate=ptask.estimated_duration_min,
                              status=TaskStatus.PENDING))

        steps += _hook_steps(ptask.post_hooks, executions, "post_task", task_id)
    steps += _hook_steps(plan.post_hooks, executions, "post_plan", None)
    return steps
```

- [x] **Step 4: Render `Step`s in the table**

Add a `plan` param to `render_execution_table_with_gantt` and a Step-driven branch (keep legacy branches intact). After the signature gains `plan: Optional[Plan] = None`, add as the FIRST branch inside the function (after `add_row` is defined):

```python
    if plan is not None:
        for step in build_steps(plan, tasks, executions or []):
            add_row(step.name, step.status, step.started_at, step.ended_at, step.estimate)
        return table
```

- [x] **Step 5: Pass the plan from the CLI**

In `cli/job.py` (around line 308-317), load the cached plan and pass it:

```python
            if job_obj.tasks:
                console.print(f"\n[bold]Task Progress[/bold]")
                executions = db.list_executions_for_job(job_obj.id)
                plan = job_controller.store.load_plan(job_obj.id)
                table = render_execution_table_with_gantt(
                    job_obj, job_obj.tasks, plan=plan,
                    now=datetime.utcnow(), executions=executions,
                )
```

- [x] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_terminal_ui.py -q`
Expected: PASS (new Step tests + existing legacy tests).

- [x] **Step 7: Commit**

```bash
git add src/minimise/interfaces/terminal_ui.py src/minimise/interfaces/cli/job.py tests/test_terminal_ui.py
git commit -m "feat: Step view-model — Gantt renders hooks by name in plan order"
```

---

### Task 8: Job-total estimate sums hooks

**Files:**
- Modify: `src/minimise/interfaces/cli/job.py:236-237, 277, 287` (`est_total`)
- Test: `tests/test_cli.py` (or a focused test on the sum)

**Interfaces:**
- Consumes: `Plan` hook lists (T2).
- Produces: `est_total` includes every hook's `estimated_duration_min` (plan-level + per-task), no double-count.

**Note:** `est_total` today sums `job_obj.tasks` durations. Hooks live in the plan, not the DB tasks — load the cached plan and add hook estimates. Guard: a job whose plan file is missing should still render (fall back to task-only sum).

- [x] **Step 1: Write the failing test**

Add a focused helper test. First extract the sum into a pure function `job_estimate_total(tasks, plan) -> int` in `cli/job.py`, then:

```python
def test_job_estimate_total_includes_hooks():
    from minimise.interfaces.cli.job import job_estimate_total
    from minimise.models import Plan, Task
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "init", "command": "true", "estimated_duration_min": 2}],
        "tasks": [{"id": "t1", "name": "B", "description": "d", "goal": "g",
                   "estimated_duration_min": 3,
                   "post_hooks": [{"name": "pytest", "command": "p", "estimated_duration_min": 4}]}],
    })
    tasks = [Task(id="task-1", job_id="j1", name="B", description="d",
                  estimated_duration_min=3, goal="g")]
    assert job_estimate_total(tasks, plan) == 2 + 3 + 4
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_cli.py::test_job_estimate_total_includes_hooks -q`
Expected: FAIL — `cannot import name 'job_estimate_total'`.

- [x] **Step 3: Implement**

In `cli/job.py`, add the helper:

```python
def job_estimate_total(tasks, plan=None) -> int:
    """Sum of task estimates plus every hook estimate (plan- and task-level)."""
    total = sum(t.estimated_duration_min for t in tasks)
    if plan is not None:
        total += sum(h.estimated_duration_min for h in plan.pre_hooks + plan.post_hooks)
        for pt in plan.tasks:
            total += sum(h.estimated_duration_min for h in pt.pre_hooks + pt.post_hooks)
    return total
```

Replace line 237 `est_total = sum(...)` with a plan-aware load:

```python
        try:
            plan = job_controller.store.load_plan(job_obj.id)
        except Exception:
            plan = None
        est_total = job_estimate_total(job_obj.tasks, plan)
```

(`tasks_summary.estimated_duration_min` at line 277 and the `Estimated Duration` print at 287 already use `est_total` — no further change.)

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_cli.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/minimise/interfaces/cli/job.py tests/test_cli.py
git commit -m "feat: job-total estimate sums hook durations too"
```

---

### Task 9: Migrate docs off the old string hook fields

**Files:**
- Modify: `examples/refactor-plan-model.yaml` (the doc text at lines 17, 45 references old fields)
- Modify: `README.md:261` (hooks mention) + add a plan-format blurb

**Interfaces:** None (docs only).

**Note:** No example currently *uses* `post_task_hook:` as a live plan field — the only references are inside `description:` prose in `refactor-plan-model.yaml`. So this is a text edit, not a plan migration that would break parsing. Verify with grep that no example YAML has a top-level/task-level `pre_plan_hook`/`post_plan_hook`/`pre_task_hook`/`post_task_hook` key.

- [ ] **Step 1: Verify no example uses the old fields as live keys**

Run: `grep -rnE '^\s*(pre|post)_(plan|task)_hook\s*:' examples/`
Expected: no matches (only in-prose mentions). If any live key exists, convert it to the `post_hooks:` list form.

- [ ] **Step 2: Update the prose in `examples/refactor-plan-model.yaml`**

Line 17: change `pre_plan_hook, post_plan_hook` → `pre_hooks, post_hooks`.
Line 45: change `briefing/hooks off the Plan` → `briefing and pre_hooks/post_hooks off the Plan` (no functional change — it's a description of a past refactor; keep it accurate to current field names).

- [ ] **Step 3: Add a hooks blurb to README**

After line 261 (the Task Executor bullet), add a short plan-format section:

```markdown
### Hooks

A hook is a named, timed step that runs a shell command in your project's
environment. Add `pre_hooks:` / `post_hooks:` lists at the plan level (run
before/after the whole job) or under any task (run before/after that task):

\`\`\`yaml
tasks:
  - id: build
    name: Build feature
    estimated_duration_min: 25
    post_hooks:
      - name: Run tests
        command: "pytest -q"
        estimated_duration_min: 3
\`\`\`

Each hook shows on the Gantt by name with its estimate, and its output is
queryable via `mini job logs --query`. A failed `post_hook` fails the task.
```

(Use real triple-backticks in the README — the `\`\`\`` above is escaped for this plan only.)

- [ ] **Step 4: Refresh stale `execution_id` fixtures (hygiene)**

These tests hardcode the OLD `execution_id` format but are self-contained (they echo/feed the string, never parse it) — so they pass either way, leaving dead-format strings in the suite. Update them to the new readable format so the codebase has one canonical shape:
- `tests/test_harness.py:214,222` — `"job#x#type#task"` → `"job#j1#task#task-9f#attempt#0"`.
- `tests/test_cli.py:951-953` — the synthetic JSONL `execution_id` values (`"job#a#task#task-aa"` etc.) are already close to the new format and the query layer treats them as opaque; leave unless they read oddly. No assertion depends on the format.

Run after editing: `PYTHONPATH=src pytest tests/test_harness.py tests/test_cli.py -q` → PASS.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ -q`
Expected: PASS (~342 + new tests; the 2 known dirty-tree `test_cli.py` env failures, if present, are out of scope).

- [ ] **Step 6: Commit**

```bash
git add examples/refactor-plan-model.yaml README.md tests/test_harness.py tests/test_cli.py
git commit -m "docs: migrate hook references to pre_hooks/post_hooks lists"
```

---

## Self-Review

**Spec coverage** (each design section → task):
- P1 BUG-4 (project env) → Task 4 + Task 5.
- P2 (named & timed) → Task 1 (`Hook`), Task 7 (rendered by name + estimate).
- P3 (shape with command-or-name) → Task 2 (schema), Task 5 (one branch on "has command", enforced at parse).
- RIPPLE #1 (`hook_name` identity + new `execution_id`) → Task 1 + Task 3.
- RIPPLE #2 (plan-order rows) → Task 7 (`build_steps` iterates plan).
- RIPPLE #3 (hook output → job log) → Task 5 (`backend.record`, `level="error"`).
- Q3 (match on `(execution_type, task_id, hook_name)`) → Task 7 (`_match_hook`).
- Abstractions (`Hook`, `Step`, `Execution.hook_name`, Plan lists, `run_shell_command env`, `HookExecutor` consolidation) → Tasks 1,2,3,4,5,6,7.
- Edge cases: dup names rejected → Task 2; failed post-hook fails task → Task 6; estimate sums hooks → Task 8; no venv falls back → Task 4; bare-name-no-command rejected → Task 2; docs migration → Task 9.
- Explicitly NOT built (config-hook resolver, type enum, review service, retries/timeout) → absent from every task. ✓

**Type consistency:** `Hook(name, estimated_duration_min, command)` constructed identically in Tasks 1,2,5,6,7,8. `HookExecutor.run(hook, execution_type, task_id)` signature used identically in Tasks 5 and 6. `Step(name, estimate, status, started_at, ended_at)` defined and consumed in Task 7. `execution_type` values (`pre_plan`/`post_plan`/`pre_task`/`post_task`) consistent across Tasks 1,5,6,7.

**Decision baked in:** `Hook` is a single **pydantic `BaseModel`** (Task 1) used directly as a typed `Plan`/`PlanTask` field. The alternative (a `@dataclass` + a separate `HookSpec` pydantic mirror with conversion at the boundary) is more machinery for no gain — YAGNI. Flagging for the reviewer in case the codebase has a convention against pydantic models leaking into the orchestration layer; if so, the conversion-at-boundary variant is the fallback.
