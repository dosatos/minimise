from minimise.models import Job, Task, Execution, JobStatus, TaskStatus
from minimise.utils import new_id
from datetime import datetime
import uuid

def test_init_db(db):
    """Test database initialization."""
    # Should not raise an error
    db.init_db()

def test_create_and_get_job(db):
    """Test creating and retrieving a job."""
    job = Job(
        id=str(uuid.uuid4()),
        name="Test Job",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml",
    )
    db.create_job(job)

    retrieved = db.get_job(job.id)
    assert retrieved is not None
    assert retrieved.name == "Test Job"
    assert retrieved.status == JobStatus.PENDING

def test_list_jobs(db):
    """Test listing all jobs."""
    job1 = Job(id=str(uuid.uuid4()), name="Job 1", status=JobStatus.PENDING)
    job2 = Job(id=str(uuid.uuid4()), name="Job 2", status=JobStatus.RUNNING)

    db.create_job(job1)
    db.create_job(job2)

    jobs = db.list_jobs()
    assert len(jobs) == 2
    assert {j.name for j in jobs} == {"Job 1", "Job 2"}

def test_list_jobs_with_limit(db):
    """Test listing jobs with limit parameter returns most recent jobs first."""
    import time

    # Create 5 jobs with small delays to ensure different timestamps
    job_ids = []
    for i in range(5):
        job = Job(id=str(uuid.uuid4()), name=f"Job {i}", status=JobStatus.PENDING)
        db.create_job(job)
        job_ids.append((job.id, job.name, job.created_at))
        time.sleep(0.01)  # Small delay to ensure ordering

    # Test limit=2 returns 2 most recent jobs
    jobs = db.list_jobs(limit=2)
    assert len(jobs) == 2
    assert jobs[0].name == "Job 4"
    assert jobs[1].name == "Job 3"

    # Test limit=3 returns 3 most recent jobs
    jobs = db.list_jobs(limit=3)
    assert len(jobs) == 3
    assert jobs[0].name == "Job 4"
    assert jobs[1].name == "Job 3"
    assert jobs[2].name == "Job 2"

def test_list_jobs_ordering_desc(db):
    """Test that list_jobs returns jobs ordered by created_at DESC (newest first)."""
    import time

    # Create 3 jobs
    jobs_created = []
    for i in range(3):
        job = Job(id=str(uuid.uuid4()), name=f"Job {i}", status=JobStatus.PENDING)
        db.create_job(job)
        jobs_created.append(job)
        time.sleep(0.01)

    # List all jobs and verify they're in DESC order
    jobs = db.list_jobs()
    assert len(jobs) == 3
    assert jobs[0].id == jobs_created[2].id  # Most recent first
    assert jobs[1].id == jobs_created[1].id
    assert jobs[2].id == jobs_created[0].id  # Oldest last

def test_list_jobs_limit_none_returns_all(db):
    """Test that list_jobs with limit=None returns all jobs (backward compatibility)."""
    for i in range(5):
        job = Job(id=str(uuid.uuid4()), name=f"Job {i}", status=JobStatus.PENDING)
        db.create_job(job)

    # Both should return all jobs
    jobs_default = db.list_jobs()
    jobs_explicit_none = db.list_jobs(limit=None)
    assert len(jobs_default) == 5
    assert len(jobs_explicit_none) == 5

def test_update_job_status(db):
    """Test updating job status."""
    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    db.update_job_status(job.id, JobStatus.RUNNING)
    updated = db.get_job(job.id)
    assert updated.status == JobStatus.RUNNING

def test_create_and_get_task(db):
    """Test creating and retrieving a task."""
    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job.id,
        name="Test Task",
        description="A test task",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    retrieved = db.get_task(task.id)
    assert retrieved is not None
    assert retrieved.name == "Test Task"
    assert retrieved.job_id == job.id

def test_update_task_status(db):
    """Test updating task status."""
    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job.id, name="Test Task", description="", status=TaskStatus.PENDING)
    db.create_task(task)

    db.update_task_status(task.id, TaskStatus.COMPLETED, retries=0)
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.COMPLETED

def test_list_tasks_for_job(db):
    """Test listing tasks for a specific job."""
    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    task1 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job.id, name="Task 1", description="", status=TaskStatus.PENDING)
    task2 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job.id, name="Task 2", description="", status=TaskStatus.PENDING)

    db.create_task(task1)
    db.create_task(task2)

    tasks = db.list_tasks_for_job(job.id)
    assert len(tasks) == 2
    assert {t.name for t in tasks} == {"Task 1", "Task 2"}

def test_delete_job_removes_disk_files(db, temp_db_dir):
    """Test that delete_job removes both database records and disk files."""
    from pathlib import Path

    # Create a job
    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    # Create a jobs directory structure
    jobs_dir = temp_db_dir / "jobs"
    job_dir = jobs_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "plan.yaml").write_text("test: plan")
    (job_dir / "base_commit.txt").write_text("abc123")

    # Verify job directory exists
    assert job_dir.exists()
    assert (job_dir / "plan.yaml").exists()

    # Delete job with jobs_dir parameter
    success = db.delete_job(job.id, jobs_dir=jobs_dir)

    # Verify deletion succeeded
    assert success

    # Verify job record is deleted from database
    assert db.get_job(job.id) is None

    # Verify job directory is removed from disk
    assert not job_dir.exists()


def test_job_timing_preservation(db):
    """Test that job timing fields are preserved across updates.

    Verifies the fix for the timing capture issue: when updating job status
    without providing started_at or completed_at, existing values should be preserved.
    """
    import time

    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    # Initial state - no timing
    job1 = db.get_job(job.id)
    assert job1.started_at is None
    assert job1.completed_at is None

    # Update to RUNNING with started_at
    start_time = datetime.utcnow()
    time.sleep(0.01)
    db.update_job_status(job.id, JobStatus.RUNNING, started_at=start_time)

    job2 = db.get_job(job.id)
    assert job2.status == JobStatus.RUNNING
    assert job2.started_at == start_time
    assert job2.completed_at is None

    # Update to COMPLETED with completed_at (should preserve started_at)
    time.sleep(0.01)
    end_time = datetime.utcnow()
    db.update_job_status(job.id, JobStatus.COMPLETED, completed_at=end_time)

    job3 = db.get_job(job.id)
    assert job3.status == JobStatus.COMPLETED
    assert job3.started_at == start_time, "started_at should be preserved"
    assert job3.completed_at == end_time
    assert job3.completed_at > job3.started_at


def test_task_timing_preservation(db):
    """Test that task timing fields are preserved across updates.

    Verifies the fix for the timing capture issue: when updating task status
    without providing started_at or completed_at, existing values should be preserved.
    """
    import time

    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job.id,
        name="Test Task",
        description="",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Initial state - no timing
    task1 = db.get_task(task.id)
    assert task1.started_at is None
    assert task1.completed_at is None

    # Update to RUNNING with started_at
    start_time = datetime.utcnow()
    time.sleep(0.01)
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=start_time)

    task2 = db.get_task(task.id)
    assert task2.status == TaskStatus.RUNNING
    assert task2.started_at == start_time
    assert task2.completed_at is None

    # Update to COMPLETED with completed_at (should preserve started_at)
    time.sleep(0.01)
    end_time = datetime.utcnow()
    db.update_task_status(
        task.id,
        TaskStatus.COMPLETED,
        retries=0,
        completed_at=end_time,
    )

    task3 = db.get_task(task.id)
    assert task3.status == TaskStatus.COMPLETED
    assert task3.started_at == start_time, "started_at should be preserved"
    assert task3.completed_at == end_time
    assert task3.completed_at > task3.started_at


def _row(cols, values):
    """Build a sqlite3.Row over an ad-hoc SELECT (no table needed)."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    select = ", ".join(f"? AS {c}" for c in cols)
    return conn.execute(f"SELECT {select}", values).fetchone()


def test_row_to_job_roundtrip(db):
    """_row_to_job reconstructs a stored job with identical field values."""
    from minimise.storage.database import _row_to_job
    job = Job(
        id=str(uuid.uuid4()), name="Job RT", status=JobStatus.RUNNING,
        plan_path="/p.yaml", base_commit="abc123", pid=4242,
    )
    db.create_job(job)

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job.id,)).fetchone()
    conn.close()

    mapped = _row_to_job(row)
    assert mapped == db.get_job(job.id)
    assert mapped.name == "Job RT"
    assert mapped.status == JobStatus.RUNNING
    assert mapped.base_commit == "abc123"
    assert mapped.pid == 4242


def test_row_to_job_missing_pid_column(db):
    """Locks the KeyError fix: an absent 'pid' column yields pid=None, not a crash."""
    from minimise.storage.database import _row_to_job
    row = _row(
        ["id", "name", "status", "plan_path", "base_commit", "created_at",
         "started_at", "completed_at"],
        ["j1", "n", "pending", "", None, "2026-01-01T00:00:00", None, None],
    )
    assert "pid" not in row.keys()
    job = _row_to_job(row)
    assert job.pid is None


def test_row_to_task_roundtrip(db):
    """_row_to_task reconstructs a stored task with identical field values."""
    from minimise.storage.database import _row_to_task
    job = Job(id=str(uuid.uuid4()), name="J", status=JobStatus.PENDING)
    db.create_job(job)
    task = Task(
        estimated_duration_min=9, id=str(uuid.uuid4()), job_id=job.id,
        name="Task RT", description="d", status=TaskStatus.COMPLETED,
        retries=2, base_commit="def456", goal="ship it",
    )
    db.create_task(task)

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task.id,)).fetchone()
    conn.close()

    mapped = _row_to_task(row)
    assert mapped == db.get_task(task.id)
    assert mapped.name == "Task RT"
    assert mapped.status == TaskStatus.COMPLETED
    assert mapped.base_commit == "def456"
    assert mapped.goal == "ship it"
    assert mapped.estimated_duration_min == 9


def test_row_to_task_missing_optional_columns(db):
    """Absent base_commit/goal/estimated_duration_min map to None/None/5, not a crash."""
    from minimise.storage.database import _row_to_task
    row = _row(
        ["id", "job_id", "name", "description", "status", "output", "retries",
         "created_at", "started_at", "completed_at", "diff_path"],
        ["t1", "j1", "n", "d", "pending", None, 0, "2026-01-01T00:00:00",
         None, None, None],
    )
    keys = row.keys()
    assert "base_commit" not in keys and "goal" not in keys
    assert "estimated_duration_min" not in keys
    task = _row_to_task(row)
    assert task.base_commit is None
    assert task.goal is None
    assert task.estimated_duration_min == 5


def test_existing_null_duration_is_backfilled(tmp_path):
    """A legacy row with NULL estimated_duration_min is backfilled to 5 on init_db."""
    import sqlite3
    from minimise.storage.database import Database

    db_path = tmp_path / "legacy.db"
    # Model a pre-migration DB: create a nullable-column tasks table by hand and
    # insert a legacy NULL row (init_db's migrated column is NOT NULL and would reject it).
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
            plan_path TEXT, base_commit TEXT, created_at TEXT NOT NULL,
            started_at TEXT, completed_at TEXT, pid INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT, status TEXT NOT NULL, output TEXT,
            retries INTEGER DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT,
            completed_at TEXT, diff_path TEXT, base_commit TEXT, goal TEXT,
            estimated_duration_min INTEGER DEFAULT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
    """)
    conn.execute("INSERT INTO jobs (id, name, status, created_at) "
                 "VALUES ('j1','n','pending','2026-01-01T00:00:00')")
    conn.execute("INSERT INTO tasks (id, job_id, name, status, created_at, estimated_duration_min) "
                 "VALUES ('t1','j1','n','pending','2026-01-01T00:00:00', NULL)")
    conn.commit()
    conn.close()

    Database(db_path).init_db()

    conn = sqlite3.connect(db_path)
    val = conn.execute("SELECT estimated_duration_min FROM tasks WHERE id='t1'").fetchone()[0]
    conn.close()
    assert val == 5


def test_duration_column_is_not_null(tmp_path):
    """After init_db, the estimated_duration_min column is NOT NULL (and others preserved)."""
    import sqlite3
    from minimise.storage.database import Database

    db_path = tmp_path / "fresh.db"
    Database(db_path).init_db()

    conn = sqlite3.connect(db_path)
    info = {c[1]: c for c in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    conn.close()
    # PRAGMA table_info: index 3 is the notnull flag (1 == NOT NULL).
    assert info["estimated_duration_min"][3] == 1
    # Pre-existing NOT NULLs must be preserved.
    for col in ("job_id", "name", "status", "created_at"):
        assert info[col][3] == 1, f"{col} lost its NOT NULL constraint"


def test_execution_round_trip_with_job_and_type(db):
    """A task Execution round-trips with job_id/execution_type via both list queries."""
    e = Execution(task_id="t1", attempt=0, job_id="j1", status=TaskStatus.COMPLETED,
                  started_at=datetime(2026, 1, 1, 0, 0, 0))
    db.save_execution(e)

    by_task = db.list_executions_for_task("t1")
    by_job = db.list_executions_for_job("j1")
    assert len(by_task) == 1 and len(by_job) == 1
    for got in (by_task[0], by_job[0]):
        assert got.task_id == "t1"
        assert got.job_id == "j1"
        assert got.execution_type == "task"
        assert got.attempt == 0


def test_list_executions_for_job_orders_by_started_at(db):
    """Executions for a job come back in started_at order."""
    db.save_execution(Execution(task_id="t2", attempt=0, job_id="j1",
                                started_at=datetime(2026, 1, 1, 0, 10, 0)))
    db.save_execution(Execution(task_id="t1", attempt=0, job_id="j1",
                                started_at=datetime(2026, 1, 1, 0, 5, 0)))
    execs = db.list_executions_for_job("j1")
    assert [e.task_id for e in execs] == ["t1", "t2"]


def test_fresh_db_has_new_schema(tmp_path):
    """A fresh Database creates the executions table with the new columns; round-trip works."""
    import sqlite3
    from minimise.storage.database import Database

    db_path = tmp_path / "fresh.db"
    db = Database(db_path)
    db.init_db()

    conn = sqlite3.connect(db_path)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(executions)").fetchall()}
    conn.close()
    assert {"execution_id", "job_id", "task_id", "execution_type"} <= cols

    db.save_execution(Execution(task_id="t1", attempt=0, job_id="j1"))
    assert db.list_executions_for_job("j1")[0].job_id == "j1"


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


def test_execution_exit_reason_round_trips(db):
    """An Execution's exit_reason persists and comes back via list_executions_for_task."""
    db.save_execution(Execution(task_id="t1", attempt=0, job_id="j1",
                                status=TaskStatus.FAILED, exit_reason="timeout"))
    assert db.list_executions_for_task("t1")[0].exit_reason == "timeout"


def test_create_job_with_tasks_is_atomic(db):
    """A failing task insert rolls back the whole job — no orphan job row."""
    import pytest
    job = Job(id=new_id("job"), name="Atomic Job", status=JobStatus.PENDING)
    dup = new_id("task")
    tasks = [
        Task(id=dup, job_id=job.id, name="ok", description="", status=TaskStatus.PENDING, estimated_duration_min=5),
        Task(id=dup, job_id=job.id, name="dup", description="", status=TaskStatus.PENDING, estimated_duration_min=5),
    ]
    with pytest.raises(Exception):
        db.create_job_with_tasks(job, tasks)

    assert db.get_job(job.id) is None
    assert db.list_tasks_for_job(job.id) == []


def test_create_job_with_tasks_happy_path(db):
    """All rows land in one transaction."""
    job = Job(id=new_id("job"), name="Atomic Job", status=JobStatus.PENDING)
    tasks = [
        Task(id=new_id("task"), job_id=job.id, name="a", description="", status=TaskStatus.PENDING, estimated_duration_min=5),
        Task(id=new_id("task"), job_id=job.id, name="b", description="", status=TaskStatus.PENDING, estimated_duration_min=5),
    ]
    db.create_job_with_tasks(job, tasks)
    assert db.get_job(job.id) is not None
    assert len(db.list_tasks_for_job(job.id)) == 2


def test_transaction_rolls_back_all_writes(db):
    """A failure inside transaction() leaves none of its writes."""
    import pytest
    job = Job(id=new_id("job"), name="Txn Job", status=JobStatus.PENDING)
    task = Task(id=new_id("task"), job_id=job.id, name="t", description="", status=TaskStatus.PENDING, estimated_duration_min=5)
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            db.create_job(job, conn=conn)
            db.create_task(task, conn=conn)
            raise RuntimeError("boom")
    assert db.get_job(job.id) is None
    assert db.get_task(task.id) is None


def test_transaction_commits_all_writes(db):
    """All writes inside transaction() land together."""
    job = Job(id=new_id("job"), name="Txn Job", status=JobStatus.PENDING)
    task = Task(id=new_id("task"), job_id=job.id, name="t", description="", status=TaskStatus.PENDING, estimated_duration_min=5)
    with db.transaction() as conn:
        db.create_job(job, conn=conn)
        db.create_task(task, conn=conn)
    assert db.get_job(job.id) is not None
    assert db.get_task(task.id) is not None


def test_delete_job_removes_plan_hook_rows(db):
    """delete_job removes plan-hook executions (task_id NULL)."""
    db.save_execution(Execution(task_id=None, attempt=0, job_id="J",
                                execution_type="pre_plan"))
    assert len(db.list_executions_for_job("J")) == 1
    db.delete_job("J")
    assert db.list_executions_for_job("J") == []
