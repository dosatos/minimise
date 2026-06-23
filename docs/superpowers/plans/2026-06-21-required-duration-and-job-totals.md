# Required `estimated_duration_min` + Job-Level Duration Totals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `estimated_duration_min` a genuinely required positive integer end-to-end (model, validator, DB) and surface the job-level total in `mini job list` / `mini job status` JSON and the `job status` table.

**Architecture:** Five independently-testable tasks. Enforce the data guarantee at the validator and DB boundaries and in the model type; then add display of the per-job sum. Strict TDD: failing test first, minimal implementation, green, commit.

**Tech Stack:** Python 3.9+, `click` CLI, `rich` tables, `sqlite3`, `pytest`, `dataclasses`.

## Global Constraints

- Python 3.9+ compatible.
- The full suite must stay green (baseline: **226 passing**).
- All `Task(...)` constructions use **keyword arguments** (verified: 0 positional sites) — reordering dataclass fields is safe; the only required change is adding the now-mandatory argument where missing.
- `estimated_duration_min` is always a **positive integer (`> 0`)**; legacy NULL rows backfill to **5**.
- JSON job-level total nests **inside the `tasks` object** for `job list`; for `job status` (whose `tasks` is a list) use a sibling **`tasks_summary`** object.
- Commit after each task. No Claude co-author line in commits.

---

### Task 1: Plan validator enforces positive-integer duration

**Files:**
- Modify: `src/minimise/plan_validator.py:103-108`
- Test: `tests/test_plan_validator.py`

**Interfaces:**
- Consumes: existing `PlanValidator.validate(plan: dict) -> List[ValidationIssue]`, `ValidationIssue(level, field, message)`, `ValidationLevel.ERROR`.
- Produces: validator now emits an ERROR when `estimated_duration_min` is present but not an `int > 0` (rejecting `bool`, strings, `0`, negatives).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_plan_validator.py`:

```python
def test_estimated_duration_must_be_positive_int():
    plan = {
        "name": "P",
        "tasks": [
            {"id": "t1", "name": "n", "description": "d", "goal": "g",
             "estimated_duration_min": 0},
        ],
    }
    issues = PlanValidator().validate(plan)
    assert any(i.field == "task[0].estimated_duration_min" and i.level == ValidationLevel.ERROR
               for i in issues)


def test_estimated_duration_rejects_non_int():
    for bad in ["soon", -5, True, 3.5]:
        plan = {
            "name": "P",
            "tasks": [
                {"id": "t1", "name": "n", "description": "d", "goal": "g",
                 "estimated_duration_min": bad},
            ],
        }
        issues = PlanValidator().validate(plan)
        assert any(i.field == "task[0].estimated_duration_min" and i.level == ValidationLevel.ERROR
                   for i in issues), f"expected error for {bad!r}"


def test_estimated_duration_accepts_positive_int():
    plan = {
        "name": "P",
        "tasks": [
            {"id": "t1", "name": "n", "description": "d", "goal": "g",
             "estimated_duration_min": 5},
        ],
    }
    issues = PlanValidator().validate(plan)
    assert not any(i.field == "task[0].estimated_duration_min" for i in issues)
```

(Confirm the existing import line already has `ValidationLevel`; if not, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_plan_validator.py -k estimated_duration -v`
Expected: the `_must_be_positive_int` and `_rejects_non_int` tests FAIL (current code only checks presence).

- [ ] **Step 3: Implement the value check**

Replace the block at `src/minimise/plan_validator.py:103-108`:

```python
            if "estimated_duration_min" not in task:
                issues.append(ValidationIssue(
                    ValidationLevel.ERROR,
                    f"task[{i}].estimated_duration_min",
                    f"Task {i} must have an 'estimated_duration_min' field"
                ))
            else:
                dur = task["estimated_duration_min"]
                if isinstance(dur, bool) or not isinstance(dur, int) or dur <= 0:
                    issues.append(ValidationIssue(
                        ValidationLevel.ERROR,
                        f"task[{i}].estimated_duration_min",
                        f"Task {i} 'estimated_duration_min' must be a positive integer (got {dur!r})"
                    ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_plan_validator.py -v`
Expected: PASS (all, including pre-existing validator tests).

- [ ] **Step 5: Commit**

```bash
git add src/minimise/plan_validator.py tests/test_plan_validator.py
git commit -m "feat: validate estimated_duration_min is a positive integer"
```

---

### Task 2: Make the model field required (non-optional) and fix all construction sites

**Files:**
- Modify: `src/minimise/models.py:20-35` (reorder + retype field)
- Modify: `src/minimise/database.py:305` (`_row_to_task` fallback), `src/minimise/job_manager.py:122` (plan→Task)
- Modify (add keyword arg): all 58 `Task(...)` sites missing the field across `tests/test_database.py`, `tests/test_terminal_ui.py`, `tests/test_api_server.py`, `tests/test_handover_manager.py`, `tests/test_task_executor.py`, `tests/test_cli.py`
- Test: `tests/test_models.py` (create if absent) — assert the field is mandatory

**Interfaces:**
- Consumes: `Task` dataclass.
- Produces: `Task(id, job_id, name, description, estimated_duration_min, status=..., ...)` — `estimated_duration_min: int` is now a **required, no-default** field positioned immediately after `description`. Constructing a `Task` without it raises `TypeError`.

- [ ] **Step 1: Write the failing test**

Create/append `tests/test_models.py`:

```python
import pytest
from minimise.models import Task


def test_task_requires_estimated_duration_min():
    with pytest.raises(TypeError):
        Task(id="t1", job_id="j1", name="n", description="d")


def test_task_accepts_estimated_duration_min():
    t = Task(id="t1", job_id="j1", name="n", description="d", estimated_duration_min=5)
    assert t.estimated_duration_min == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: `test_task_requires_estimated_duration_min` FAILS (field currently has a default, so no `TypeError`).

- [ ] **Step 3: Retype and reorder the model field**

In `src/minimise/models.py`, remove the old last line
`estimated_duration_min: Optional[int] = field(default_factory=lambda: None)`
and insert the field as required, right after `description`:

```python
@dataclass
class Task:
    id: str
    job_id: str
    name: str
    description: str
    estimated_duration_min: int
    status: TaskStatus = TaskStatus.PENDING
    output: Optional[str] = None
    retries: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    diff_path: Optional[str] = None
    base_commit: Optional[str] = None
    goal: Optional[str] = None
```

- [ ] **Step 4: Fix the src construction/mapping sites**

There is NO `_row_to_task` helper — `database.py` has **two** inline row→`Task` constructions, and **both** need the coalesce (the CLI `job list`/`job status` paths call `list_tasks_for_job`, so missing the second causes a `TypeError` in Tasks 4 & 5). Fix `get_task` (~line 305) **and** `list_tasks_for_job` (~line 332), each identically:

```python
            estimated_duration_min=(
                row['estimated_duration_min']
                if 'estimated_duration_min' in row.keys() and row['estimated_duration_min'] is not None
                else 5
            ),
```

`src/minimise/job_manager.py` (~line 130) — the `Task(...)` built from the plan currently uses `estimated_duration_min=task_config.get("estimated_duration_min")`, which returns `None` when absent. Since the field is now a required `int` and dataclasses don't enforce types at runtime, a `None` would slip through silently. Change to direct-key access `estimated_duration_min=task_config["estimated_duration_min"]` so it raises a clear `KeyError` on an unvalidated plan (the plan validator guarantees the key is present and `> 0`).

- [ ] **Step 5: Add the keyword arg to all 58 test sites**

For each site listed below, add `estimated_duration_min=5,` inside the `Task(...)` call (any positive int is fine; use 5 unless the test asserts a specific total). All sites are keyword-style, so placement within the call is free.

Sites (file:line at plan-writing time — match the `Task(` construction, not line number if shifted):
`tests/test_database.py`: 107, 126, 139, 140, 230
`tests/test_terminal_ui.py`: 43, 55, 67, 79
`tests/test_api_server.py`: 114, 122, 154
`tests/test_handover_manager.py`: 14, 42, 65, 86, 107, 134
`tests/test_task_executor.py`: 92, 143, 189, 233, 286, 334, 360
`tests/test_cli.py`: 254, 263, 302, 311, 361, 407, 449, 684, 714, 818, 828, 868, 878, 918, 993, 1002, 1046, 1055, 1091, 1188, 1197, 1324, 1383, 1418, 1447, 1465, 1549, 1557, 1708, 1716, 1760, 1865, 1872

**CRITICAL — `tests/test_job_manager.py` (indirect constructions the scanner won't catch):** its Tasks are built via `create_job(plan_file)` from plan dicts, not literal `Task(...)`. You must also:
- Add `estimated_duration_min` (positive int, e.g. 5) to the `plan_file` fixture (~lines 63-92) and every other inline plan/task dict that omits it — otherwise `create_job` hits the direct `task_config["estimated_duration_min"]` access (Step 4) and raises `KeyError`.
- **Delete** `test_estimated_duration_min_optional_field` (~line 843) — it asserts the value is `None`, contradicting the now-required field.
- **Delete** `test_estimated_duration_min_zero_value` (~line 982) — it uses/asserts `0`, now rejected by the validator and the `>0` rule.
- Keep the positive-value tests (`parsed_from_yaml`, `stored_in_database`, `survives_job_resume`).

Verification command to find any remaining bare constructions after editing (covers literal `Task(` in `tests/` only — the `test_job_manager.py` indirect sites and the two `database.py` src sites are verified by the full green suite):

```bash
python3 - <<'PY'
import re, glob
miss=0
for f in glob.glob("tests/*.py"):
    s=open(f).read()
    for m in re.finditer(r'(?<![\w.])Task\(', s):
        pre=s[max(0,m.start()-6):m.start()]
        if pre.endswith(('Status','ecutor')): continue
        i=m.end(); d=1; j=i
        while d>0 and j<len(s):
            d+= s[j]=='('; d-= s[j]==')'; j+=1
        if 'estimated_duration_min' not in s[m.start():j]:
            miss+=1; print(f"{f}:{s[:m.start()].count(chr(10))+1}")
print("remaining missing:", miss)
PY
```

Expected after edits: `remaining missing: 0`.

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/ -q`
Expected: PASS — `test_models.py` green and no `TypeError` regressions elsewhere. (DB tests still pass: the column is still nullable until Task 3, and `_row_to_task` now coalesces NULL→5.)

- [ ] **Step 7: Commit**

```bash
git add src/minimise/models.py src/minimise/database.py src/minimise/job_manager.py tests/
git commit -m "feat: make Task.estimated_duration_min a required int; update construction sites"
```

---

### Task 3: Database backfill + NOT NULL column

**Files:**
- Modify: `src/minimise/database.py:52` (fresh-schema CREATE TABLE), `:69-73` (migration block)
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: `Database(db_path)`, `Database.init_db()`, existing `PRAGMA table_info(tasks)` migration pattern.
- Produces: after `init_db()`, the `tasks.estimated_duration_min` column is NOT NULL and any pre-existing NULL rows have been backfilled to `5`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_database.py`:

```python
def test_existing_null_duration_is_backfilled(tmp_path):
    import sqlite3
    from minimise.database import Database
    db_path = tmp_path / "legacy.db"
    db = Database(db_path)
    db.init_db()
    # Simulate a legacy row with NULL duration by writing directly.
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO jobs (id, name, status, plan_path, created_at) "
                 "VALUES ('j1','n','pending','p','2026-01-01T00:00:00')")
    conn.execute("INSERT INTO tasks (id, job_id, name, description, status, estimated_duration_min) "
                 "VALUES ('t1','j1','n','d','pending', NULL)")
    conn.commit(); conn.close()
    # Re-run init_db (idempotent migration) to trigger backfill.
    db.init_db()
    conn = sqlite3.connect(db_path)
    val = conn.execute("SELECT estimated_duration_min FROM tasks WHERE id='t1'").fetchone()[0]
    conn.close()
    assert val == 5


def test_duration_column_is_not_null(tmp_path):
    import sqlite3
    from minimise.database import Database
    db_path = tmp_path / "fresh.db"
    db = Database(db_path); db.init_db()
    conn = sqlite3.connect(db_path)
    info = conn.execute("PRAGMA table_info(tasks)").fetchall()
    conn.close()
    col = [c for c in info if c[1] == "estimated_duration_min"][0]
    # PRAGMA table_info: index 3 is "notnull" (1 == NOT NULL)
    assert col[3] == 1
```

Note: the legacy-insert test must run *before* the NOT NULL rebuild on that DB; `init_db()` performs backfill **then** the NOT NULL rebuild, so the direct NULL insert above must happen against the still-nullable schema. If the fresh `init_db()` already makes the column NOT NULL, adjust the test to insert the NULL row via a manually-created pre-migration table. Implementer: see Step 3 ordering.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database.py -k "null or not_null" -v`
Expected: `test_duration_column_is_not_null` FAILS (column currently `DEFAULT NULL`, notnull=0).

- [ ] **Step 3: Implement backfill + NOT NULL rebuild**

(a) Fresh schema at `database.py:52` — change the column definition in the `CREATE TABLE tasks` to:

```python
                estimated_duration_min INTEGER NOT NULL DEFAULT 5,
```

(b) In the migration block (after the existing add-column step at ~line 73), add backfill then a guarded rebuild. SQLite cannot alter a column to NOT NULL in place:

Make the whole sequence atomic so a mid-rebuild failure rolls back and never leaves the DB without a `tasks` table. **`init_db` uses a bare `sqlite3.connect()` with default `isolation_level` and a single `conn.commit()` at the end** — the driver already holds an implicit transaction, so a bare `cursor.execute("BEGIN")` would raise "cannot start a transaction within a transaction". Use a **SAVEPOINT** (safe inside an open transaction). The `tasks_new` DDL preserves **every NOT NULL the live schema has** (`job_id`, `name`, `status`, `created_at`) — changing only the duration column; `col_list` is derived from `tasks_new`:

```python
        cursor.execute("SAVEPOINT dur_migration")
        try:
            # Backfill legacy NULL durations before enforcing NOT NULL.
            cursor.execute(
                "UPDATE tasks SET estimated_duration_min = 5 WHERE estimated_duration_min IS NULL"
            )

            # Enforce NOT NULL via table rebuild only if the live column still allows NULL.
            cursor.execute("PRAGMA table_info(tasks)")
            dur = next((c for c in cursor.fetchall() if c[1] == "estimated_duration_min"), None)
            if dur is not None and dur[3] == 0:  # notnull flag is 0 -> needs rebuild
                # EXACT current schema, only the duration column changed. Re-read
                # database.py:37-55 and confirm before pasting; keep all NOT NULLs.
                cursor.execute("""
                    CREATE TABLE tasks_new (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL,
                        output TEXT,
                        retries INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        diff_path TEXT,
                        base_commit TEXT,
                        goal TEXT,
                        estimated_duration_min INTEGER NOT NULL DEFAULT 5,
                        FOREIGN KEY(job_id) REFERENCES jobs(id)
                    )
                """)
                # Derive col_list from the NEW table so columns stay aligned.
                cursor.execute("PRAGMA table_info(tasks_new)")
                col_list = ", ".join(c[1] for c in cursor.fetchall())
                cursor.execute(f"INSERT INTO tasks_new ({col_list}) SELECT {col_list} FROM tasks")
                cursor.execute("DROP TABLE tasks")
                cursor.execute("ALTER TABLE tasks_new RENAME TO tasks")
            cursor.execute("RELEASE SAVEPOINT dur_migration")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT dur_migration")
            raise
        # The existing conn.commit() at the end of init_db commits the released savepoint.
```

**Important:** confirm the DDL still matches the live `CREATE TABLE tasks` (`database.py:37-55`) before pasting — if the schema has drifted, use the live definitions with all NOT NULLs intact, changing only the duration column.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`
Expected: PASS (backfill sets 5; column notnull=1).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: PASS (226+ new tests).

- [ ] **Step 6: Commit**

```bash
git add src/minimise/database.py tests/test_database.py
git commit -m "feat: backfill NULL durations to 5 and make estimated_duration_min NOT NULL"
```

---

### Task 4: `mini job list --format json` includes job-level total

**Files:**
- Modify: `src/minimise/cli.py:231-242`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `db.list_tasks_for_job(job_id)` (already called at `cli.py:255` for the table), each task's `.estimated_duration_min: int`.
- Produces: each job object in `job list --format json` has `tasks: { total, completed, estimated_duration_min }`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (follow the existing fixture style — `runner`, `mock_config_dir`, `Database`, `Job`, `Task`):

```python
def test_job_list_json_includes_duration_total(runner, mock_config_dir):
    from minimise.models import Task, TaskStatus
    db = Database(mock_config_dir / "minimise.db"); db.init_db()
    job = Job(id=str(uuid.uuid4()), name="J", status=JobStatus.PENDING,
              plan_path="/p.yaml", created_at=datetime.utcnow())
    db.create_job(job)
    db.create_task(Task(id="t1", job_id=job.id, name="a", description="d",
                        estimated_duration_min=30, status=TaskStatus.PENDING))
    db.create_task(Task(id="t2", job_id=job.id, name="b", description="d",
                        estimated_duration_min=45, status=TaskStatus.PENDING))
    result = runner.invoke(mini, ["job", "list", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    job_obj = [j for j in data if j["id"] == job.id][0]
    assert job_obj["tasks"]["estimated_duration_min"] == 75
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_job_list_json_includes_duration_total -v`
Expected: FAIL with `KeyError: 'estimated_duration_min'`.

- [ ] **Step 3: Implement**

The JSON branch loops jobs but currently does not fetch tasks for each. Inside the `for j in jobs:` loop of the JSON branch (around `cli.py:225-242`), fetch tasks and add the total. Read `cli.py:215-242` first to find where `task_count`/`completed_count` are computed in the JSON branch and reuse them. Update the appended dict's `tasks` object:

```python
                job_tasks = db.list_tasks_for_job(j.id)
                est_total = sum(t.estimated_duration_min for t in job_tasks)
                jobs_data.append({
                    "id": j.id,
                    "name": j.name,
                    "status": j.status.value,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                    "tasks": {
                        "total": len(job_tasks),
                        "completed": sum(1 for t in job_tasks if t.status == TaskStatus.COMPLETED),
                        "estimated_duration_min": est_total,
                    },
                })
```

(If `task_count`/`completed_count` are already computed just above from a fetched list, reuse that list instead of re-querying — do not double-fetch.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_job_list_json_includes_duration_total -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/minimise/cli.py tests/test_cli.py
git commit -m "feat: add estimated_duration_min total to job list JSON"
```

---

### Task 5: `mini job status` job-level total (JSON `tasks_summary` + table line)

**Files:**
- Modify: `src/minimise/cli.py:329-338` (JSON output dict), `:340-357` (table details)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `job_obj.tasks` (list of `Task`), each `.estimated_duration_min: int`.
- Produces: `job status --format json` gains a sibling `tasks_summary: { total, completed, estimated_duration_min }` (the existing `tasks` list is unchanged). The table output gains an `Estimated Duration: N min` line.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_job_status_json_includes_duration_summary(runner, mock_config_dir):
    from minimise.models import Task, TaskStatus
    db = Database(mock_config_dir / "minimise.db"); db.init_db()
    job = Job(id=str(uuid.uuid4()), name="J", status=JobStatus.PENDING,
              plan_path="/p.yaml", created_at=datetime.utcnow())
    db.create_job(job)
    db.create_task(Task(id="t1", job_id=job.id, name="a", description="d",
                        estimated_duration_min=20, status=TaskStatus.PENDING))
    db.create_task(Task(id="t2", job_id=job.id, name="b", description="d",
                        estimated_duration_min=25, status=TaskStatus.PENDING))
    result = runner.invoke(mini, ["job", "status", job.id, "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data["tasks"], list)            # unchanged
    assert data["tasks_summary"]["estimated_duration_min"] == 45
    assert data["tasks_summary"]["total"] == 2


def test_job_status_table_shows_duration_total(runner, mock_config_dir):
    from minimise.models import Task, TaskStatus
    db = Database(mock_config_dir / "minimise.db"); db.init_db()
    job = Job(id=str(uuid.uuid4()), name="J", status=JobStatus.PENDING,
              plan_path="/p.yaml", created_at=datetime.utcnow())
    db.create_job(job)
    db.create_task(Task(id="t1", job_id=job.id, name="a", description="d",
                        estimated_duration_min=40, status=TaskStatus.PENDING))
    result = runner.invoke(mini, ["job", "status", job.id])
    assert result.exit_code == 0
    assert "Estimated Duration" in result.output
    assert "40" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k "duration_summary or table_shows_duration" -v`
Expected: both FAIL (`KeyError: 'tasks_summary'`; table has no such line).

- [ ] **Step 3: Implement JSON `tasks_summary`**

In the `format == "json"` branch of `job_status`, after building `tasks_data` and before/within constructing `output` (around `cli.py:329-338`), add the summary as a sibling key:

```python
            est_total = sum(t.estimated_duration_min for t in job_obj.tasks)
            output = {
                "id": job_obj.id,
                "name": job_obj.name,
                "status": job_obj.status.value,
                "created_at": job_obj.created_at.isoformat() if job_obj.created_at else None,
                "started_at": job_obj.started_at.isoformat() if job_obj.started_at else None,
                "completed_at": job_obj.completed_at.isoformat() if job_obj.completed_at else None,
                "tasks": tasks_data,
                "tasks_summary": {
                    "total": len(job_obj.tasks),
                    "completed": sum(1 for t in job_obj.tasks if t.status == TaskStatus.COMPLETED),
                    "estimated_duration_min": est_total,
                },
            }
```

- [ ] **Step 4: Implement the table line**

In the table branch (after the `Status:` line, ~`cli.py:344`), add:

```python
            est_total = sum(t.estimated_duration_min for t in job_obj.tasks)
            console.print(f"[bold]Estimated Duration:[/bold] {est_total} min")
```

(Place it among the other detail lines; the sum is `0` when there are no tasks.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k "duration_summary or table_shows_duration" -v`
Expected: PASS. Also confirm the pre-existing `test_job_status_json_includes_timing` still passes (the `tasks` list shape is unchanged).

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/minimise/cli.py tests/test_cli.py
git commit -m "feat: add job-level duration total to job status JSON and table"
```

---

## Self-Review

**Spec coverage:**
- A1 validator >0 int → Task 1 ✓
- A2 model non-optional + reorder + sites → Task 2 ✓
- A3 backfill + NOT NULL → Task 3 ✓
- B1 job list JSON total inside `tasks` → Task 4 ✓
- B2 job status JSON total (`tasks_summary` sibling) → Task 5 ✓
- B3 job status table line → Task 5 ✓
- Testing section requirements → covered across Tasks 1–5 ✓

**Type consistency:** `estimated_duration_min: int` used uniformly; field name identical everywhere; `tasks.estimated_duration_min` (list cmd) vs `tasks_summary.estimated_duration_min` (status cmd) divergence is intentional and documented in Global Constraints + spec B2.

**Placeholder scan:** No TBD/TODO; every code step shows concrete code and exact commands. The two implementation-judgment notes (verify live `CREATE TABLE` columns in Task 3; reuse already-fetched task list in Task 4) point to reading specific existing lines, not deferred work.
