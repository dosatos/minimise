from minimise.models import Job, Task, JobStatus, TaskStatus
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

    task = Task(
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

    task = Task(id=str(uuid.uuid4()), job_id=job.id, name="Test Task", description="", status=TaskStatus.PENDING)
    db.create_task(task)

    db.update_task_status(task.id, TaskStatus.COMPLETED, output="Task output", retries=0)
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.COMPLETED
    assert updated.output == "Task output"

def test_list_tasks_for_job(db):
    """Test listing tasks for a specific job."""
    job = Job(id=str(uuid.uuid4()), name="Test Job", status=JobStatus.PENDING)
    db.create_job(job)

    task1 = Task(id=str(uuid.uuid4()), job_id=job.id, name="Task 1", description="", status=TaskStatus.PENDING)
    task2 = Task(id=str(uuid.uuid4()), job_id=job.id, name="Task 2", description="", status=TaskStatus.PENDING)

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
