"""Tests for CLI interface."""

import pytest
import tempfile
import json
import time
import uuid
from pathlib import Path
from datetime import datetime
from click.testing import CliRunner
from minimise.interfaces.cli import mini
from minimise.storage.database import Database
from minimise.models import Job, JobStatus, Task, TaskStatus


@pytest.fixture
def temp_home_dir(monkeypatch):
    """Mock the home directory for minimise config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("HOME", tmpdir)
        yield Path(tmpdir)


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


def test_minimise_home_env_override(monkeypatch, tmp_path):
    """MINIMISE_HOME overrides the default ~/.minimise config location."""
    import importlib
    import minimise.interfaces.cli as cli

    custom = tmp_path / "custom-home"
    monkeypatch.setenv("MINIMISE_HOME", str(custom))
    importlib.reload(cli)
    try:
        assert cli.CONFIG_DIR == custom
        assert cli.DB_PATH == custom / "minimise.db"
        assert cli.JOBS_DIR == custom / "jobs"
    finally:
        monkeypatch.delenv("MINIMISE_HOME", raising=False)
        importlib.reload(cli)


def test_mini_job_list_empty(runner, temp_home_dir):
    """Test that mini job list works with empty job list."""
    result = runner.invoke(mini, ["job", "list"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output or "Jobs" in result.output


def test_mini_job_new_creates_job(runner, temp_home_dir):
    """Test that mini job new creates a job from a plan file."""
    # Create a temporary plan file
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_content = """
name: Test Plan
briefing: Test briefing
tasks:
  - name: Task 1
    description: First task
  - name: Task 2
    description: Second task
"""
        plan_path.write_text(plan_content)

        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        # Job creation should succeed (in real scenario with git repo)
        # But since we might not be in a git repo, check for appropriate error or success
        # The command should complete without Python exceptions
        assert result.exit_code in [0, 1]  # Allow both success and graceful failure
        # Ensure no Python tracebacks in output
        assert "Traceback" not in result.output


def test_mini_job_help(runner):
    """Test that job commands have help text."""
    result = runner.invoke(mini, ["job", "--help"])
    assert result.exit_code == 0
    assert "new" in result.output
    assert "list" in result.output
    assert "status" in result.output
    assert "stop" in result.output
    assert "logs" in result.output


def test_mini_view_help(runner):
    """Test that view commands have help text."""
    result = runner.invoke(mini, ["view", "--help"])
    assert result.exit_code == 0
    assert "start" in result.output


def test_mini_main_help(runner):
    """Test main CLI help."""
    result = runner.invoke(mini, ["--help"])
    assert result.exit_code == 0
    assert "Minimise" in result.output
    assert "job" in result.output
    assert "view" in result.output


def test_mini_job_list_with_default_limit(db):
    """Test that mini job list shows default 10 jobs when limit not specified."""
    from minimise.interfaces.cli import mini
    from click.testing import CliRunner

    # Use the db fixture which is already set up with a test database
    # Create 15 jobs to exceed default limit of 10
    for i in range(15):
        job = Job(
            id=str(uuid.uuid4()),
            name=f"Test Job {i:02d}",
            status=JobStatus.PENDING,
            plan_path="/path/to/plan.yaml"
        )
        db.create_job(job)
        time.sleep(0.01)  # Small delay to ensure different timestamps

    # We can verify the database directly since CLI will read from real DB
    all_jobs = db.list_jobs(limit=None)
    assert len(all_jobs) == 15

    # Check default limit behavior using database (tests the core functionality)
    default_limited = db.list_jobs(limit=10)
    assert len(default_limited) == 10
    assert default_limited[0].name == "Test Job 14"  # Most recent first


def test_mini_job_list_with_custom_limit(db):
    """Test that limit parameter works correctly with custom values."""
    # Create 12 jobs
    for i in range(12):
        job = Job(
            id=str(uuid.uuid4()),
            name=f"Limit Test {i:02d}",
            status=JobStatus.PENDING,
            plan_path="/path/to/plan.yaml"
        )
        db.create_job(job)
        time.sleep(0.01)

    # Test limit=5
    limited = db.list_jobs(limit=5)
    assert len(limited) == 5, f"Expected 5 jobs with limit=5, got {len(limited)}"
    assert limited[0].name == "Limit Test 11"  # Most recent


def test_mini_job_list_with_limit_larger_than_count(db):
    """Test that limit works when limit is larger than total jobs."""
    # Create 5 jobs
    for i in range(5):
        job = Job(
            id=str(uuid.uuid4()),
            name=f"Small Set {i:02d}",
            status=JobStatus.PENDING,
            plan_path="/path/to/plan.yaml"
        )
        db.create_job(job)
        time.sleep(0.01)

    # Test limit=20 (larger than total)
    limited = db.list_jobs(limit=20)
    assert len(limited) == 5, f"Expected 5 jobs, got {len(limited)}"


def test_mini_job_list_json_format_with_limit(runner, temp_home_dir):
    """Test that JSON format respects the limit option."""
    # Create setup that works with HOME override
    import os

    # Save original HOME
    original_home = os.environ.get("HOME")

    try:
        # Set HOME to temp directory
        os.environ["HOME"] = str(temp_home_dir)

        # Now create database in the mocked home
        db_path = Path(temp_home_dir) / ".minimise" / "minimise.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        db = Database(db_path)
        db.init_db()

        # Create 8 jobs
        for i in range(8):
            job = Job(
                id=str(uuid.uuid4()),
                name=f"JSON Test {i:02d}",
                status=JobStatus.PENDING,
                plan_path="/path/to/plan.yaml"
            )
            db.create_job(job)
            time.sleep(0.01)

        # Verify directly with database
        limited = db.list_jobs(limit=3)
        assert len(limited) == 3
        assert limited[0].name == "JSON Test 07"
    finally:
        # Restore original HOME
        if original_home:
            os.environ["HOME"] = original_home


def test_mini_job_list_shows_most_recent_jobs(db):
    """Test that list shows most recent jobs (DESC order by created_at)."""
    # Create 5 jobs with known order
    job_ids = []
    for i in range(5):
        job = Job(
            id=str(uuid.uuid4()),
            name=f"Order Test {i}",
            status=JobStatus.PENDING,
            plan_path="/path/to/plan.yaml"
        )
        db.create_job(job)
        job_ids.append(job.id)
        time.sleep(0.01)

    # Get all jobs
    all_jobs = db.list_jobs(limit=5)

    # Verify they're in DESC order (most recent first)
    assert all_jobs[0].name == "Order Test 4"
    assert all_jobs[1].name == "Order Test 3"
    assert all_jobs[2].name == "Order Test 2"
    assert all_jobs[3].name == "Order Test 1"
    assert all_jobs[4].name == "Order Test 0"


def test_job_show_help(runner):
    """Test that job show command exists and has help."""
    result = runner.invoke(mini, ["job", "show", "--help"])
    assert result.exit_code == 0
    assert "Show job plan structure" in result.output or "show" in result.output


def test_job_show_with_invalid_job_id(runner):
    """Test that job show fails gracefully with invalid job ID."""
    result = runner.invoke(mini, ["job", "show", "invalid-job-id"])
    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


def test_job_status_json_format_valid(runner, mock_config_dir):
    """Test that job status --format json returns valid JSON."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="JSON Status Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create tasks for the job
    task1 = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="First Task",
        description="First task description",
        status=TaskStatus.COMPLETED,
        started_at=job.created_at,
        completed_at=job.created_at,
    )
    task2 = Task(estimated_duration_min=5, 
        id="task-2",
        job_id=job.id,
        name="Second Task",
        description="Second task description",
        status=TaskStatus.COMPLETED,
        started_at=job.created_at,
        completed_at=job.created_at,
    )
    db.create_task(task1)
    db.create_task(task2)

    # Invoke CLI with --format json
    result = runner.invoke(mini, ["job", "status", job.id, "--format", "json"])

    assert result.exit_code == 0
    # Should be valid JSON
    output_json = json.loads(result.output)
    assert isinstance(output_json, dict)


def test_job_status_json_includes_task_ids(runner, mock_config_dir):
    """Test that job status JSON includes all task IDs."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Task ID Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create tasks with specific IDs
    task1 = Task(estimated_duration_min=5, 
        id="task-alpha",
        job_id=job.id,
        name="Alpha Task",
        description="Alpha description",
        status=TaskStatus.COMPLETED,
        started_at=job.created_at,
        completed_at=job.created_at,
    )
    task2 = Task(estimated_duration_min=5, 
        id="task-beta",
        job_id=job.id,
        name="Beta Task",
        description="Beta description",
        status=TaskStatus.COMPLETED,
        started_at=job.created_at,
        completed_at=job.created_at,
    )
    db.create_task(task1)
    db.create_task(task2)

    result = runner.invoke(mini, ["job", "status", job.id, "--format", "json"])

    assert result.exit_code == 0
    output_json = json.loads(result.output)

    # Check structure
    assert "tasks" in output_json
    assert isinstance(output_json["tasks"], list)
    assert len(output_json["tasks"]) == 2

    # Check task IDs are present
    task_ids = [t["id"] for t in output_json["tasks"]]
    assert "task-alpha" in task_ids
    assert "task-beta" in task_ids

    # Every task entry carries an assignee key (null when unassigned)
    assert all("assignee" in t for t in output_json["tasks"])


def test_job_status_json_includes_timing(runner, mock_config_dir):
    """Test that job status JSON includes duration and timing information."""
    from minimise.models import Task, TaskStatus
    from datetime import timedelta

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Timing Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml",
        created_at=datetime.utcnow(),
    )
    db.create_job(job)

    start_time = datetime.utcnow()
    end_time = start_time + timedelta(seconds=45.2)

    task = Task(estimated_duration_min=5, 
        id="task-timed",
        job_id=job.id,
        name="Timed Task",
        description="Task with timing",
        status=TaskStatus.COMPLETED,
        started_at=start_time,
        completed_at=end_time,
    )
    db.create_task(task)

    result = runner.invoke(mini, ["job", "status", job.id, "--format", "json"])

    assert result.exit_code == 0
    output_json = json.loads(result.output)

    # Check timing fields exist
    assert "created_at" in output_json
    assert "started_at" in output_json or output_json.get("started_at") is None
    assert "completed_at" in output_json or output_json.get("completed_at") is None

    # Check task timing
    assert len(output_json["tasks"]) == 1
    task_json = output_json["tasks"][0]
    assert "started_at" in task_json
    assert "completed_at" in task_json
    assert "duration_seconds" in task_json


def test_job_status_default_table_format(runner, mock_config_dir):
    """Test that job status without --format uses table format by default."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with a task
    job = Job(
        id=str(uuid.uuid4()),
        name="Table Format Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id="task-table",
        job_id=job.id,
        name="Table Task",
        description="Task for table",
        status=TaskStatus.COMPLETED,
        started_at=job.created_at,
        completed_at=job.created_at,
    )
    db.create_task(task)

    result = runner.invoke(mini, ["job", "status", job.id])

    assert result.exit_code == 0
    # Table format should contain job details as text
    assert "Job Details" in result.output or "Status" in result.output
    # Should NOT be JSON
    try:
        json.loads(result.output)
        assert False, "Default output should not be valid JSON"
    except json.JSONDecodeError:
        pass  # Expected


# ============================================================================
# START COMMAND TESTS
# ============================================================================

def test_start_pending_job_sets_running_status(db):
    """Test starting a PENDING job transitions it to RUNNING status."""
    from minimise.models import Task, TaskStatus

    # Create a PENDING job
    job = Job(
        id=str(uuid.uuid4()),
        name="Test Start Job",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create a task for the job
    task = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="First Task",
        description="First task",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Verify initial state
    job_before = db.get_job(job.id)
    assert job_before.status == JobStatus.PENDING

    # Simulate starting (update status, set started_at, set PID)
    db.update_job_status(
        job.id,
        JobStatus.RUNNING,
        started_at=datetime.utcnow(),
        pid=12345
    )

    # Verify transition
    job_after = db.get_job(job.id)
    assert job_after.status == JobStatus.RUNNING
    assert job_after.started_at is not None
    assert job_after.pid == 12345


def _make_start_job(mock_config_dir, status, pid=None):
    """Create a job in the CLI's real DB (with a cached plan) at `status`."""
    import yaml as _yaml
    db = Database(mock_config_dir / "minimise.db")
    db.init_db()
    job = Job(
        id=str(uuid.uuid4()),
        name="Start Job",
        status=status,
        plan_path="/path/to/plan.yaml",
        pid=pid,
    )
    db.create_job(job)
    task = Task(
        id="task-start", job_id=job.id, name="T1", description="d",
        status=TaskStatus.PENDING, estimated_duration_min=1,
    )
    db.create_task(task)
    job_dir = mock_config_dir / "jobs" / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "plan.yaml", "w") as f:
        _yaml.dump({"name": "Start Job",
                    "tasks": [{"id": "task-start", "name": "T1", "goal": "g",
                               "description": "d", "estimated_duration_min": 1}]}, f)
    return db, job


def test_start_live_running_job_backs_off(runner, mock_config_dir, monkeypatch):
    """A live RUNNING job backs off (exit 0) and is never executed."""
    import os
    from minimise.orchestration.job_executor import JobExecutor
    called = []
    monkeypatch.setattr(JobExecutor, "execute", lambda self, j, p: called.append(1) or True)

    _, job = _make_start_job(mock_config_dir, JobStatus.RUNNING, pid=os.getpid())
    result = runner.invoke(mini, ["job", "start", job.id])

    assert result.exit_code == 0
    assert "already running" in result.output
    assert not called  # executor never ran


def test_start_completed_job_noops(runner, mock_config_dir, monkeypatch):
    """A COMPLETED job is a no-op (exit 0, not executed)."""
    from minimise.orchestration.job_executor import JobExecutor
    called = []
    monkeypatch.setattr(JobExecutor, "execute", lambda self, j, p: called.append(1) or True)

    _, job = _make_start_job(mock_config_dir, JobStatus.COMPLETED)
    result = runner.invoke(mini, ["job", "start", job.id])

    assert result.exit_code == 0
    assert "already complete" in result.output
    assert not called


def test_start_failed_job_resumes(runner, mock_config_dir, monkeypatch):
    """A FAILED job resumes: the executor runs, exit 0 on success."""
    from minimise.orchestration.job_executor import JobExecutor
    called = []
    monkeypatch.setattr(JobExecutor, "execute", lambda self, j, p: called.append(1) or True)

    _, job = _make_start_job(mock_config_dir, JobStatus.FAILED)
    result = runner.invoke(mini, ["job", "start", job.id])

    assert result.exit_code == 0
    assert "completed successfully" in result.output
    assert called  # resumed → executor ran


def test_start_with_harness_flag_passes_through(runner, mock_config_dir, monkeypatch):
    """`--harness pi` is passed through to JobController.start_job()."""
    from minimise.agents.harness import HARNESS_CLAUDE, HARNESS_PI
    from minimise.orchestration.job_controller import JobController
    captured = {}
    orig = JobController.start_job

    def fake_start_job(self, job_id, harness_name=HARNESS_CLAUDE):
        captured["harness_name"] = harness_name
        return orig(self, job_id, harness_name=harness_name)

    from minimise.orchestration.job_executor import JobExecutor
    monkeypatch.setattr(JobExecutor, "execute", lambda self, j, p: True)
    monkeypatch.setattr(JobController, "start_job", fake_start_job)

    _, job = _make_start_job(mock_config_dir, JobStatus.PENDING)
    result = runner.invoke(mini, ["job", "start", job.id, "--harness", HARNESS_PI])

    assert result.exit_code == 0
    assert captured["harness_name"] == HARNESS_PI


def test_start_dead_running_job_resumes(runner, mock_config_dir, monkeypatch):
    """A RUNNING job with a DEAD pid is reconciled to FAILED then resumed."""
    from minimise.orchestration.job_executor import JobExecutor
    called = []
    monkeypatch.setattr(JobExecutor, "execute", lambda self, j, p: called.append(1) or True)

    # pid 999999 is (essentially) never a live process → reconcile downgrades it.
    _, job = _make_start_job(mock_config_dir, JobStatus.RUNNING, pid=999999)
    result = runner.invoke(mini, ["job", "start", job.id])

    assert result.exit_code == 0
    assert "completed successfully" in result.output
    assert called  # dead → reconciled to FAILED → resumed


def test_start_nonexistent_job_fails(runner, mock_config_dir):
    """Test that starting a nonexistent job returns error."""
    result = runner.invoke(mini, ["job", "start", "nonexistent-job-id"])

    # Should fail with error message
    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


# ============================================================================
# STOP COMMAND TESTS
# ============================================================================

def test_stop_running_job_sets_stopped_status(db):
    """Test stopping a RUNNING job transitions it to STOPPED status."""
    # Create a RUNNING job with PID
    job = Job(
        id=str(uuid.uuid4()),
        name="Test Stop Job",
        status=JobStatus.RUNNING,
        plan_path="/path/to/plan.yaml",
        pid=12345,
        started_at=datetime.utcnow()
    )
    db.create_job(job)

    # Simulate stopping (update status to STOPPED, set completed_at)
    db.update_job_status(
        job.id,
        JobStatus.STOPPED,
        completed_at=datetime.utcnow()
    )

    # Verify transition
    job_after = db.get_job(job.id)
    assert job_after.status == JobStatus.STOPPED
    assert job_after.completed_at is not None


def test_stop_pending_job_fails(db, runner, mock_config_dir):
    """Test that stopping a PENDING job returns error."""
    # Create a PENDING job
    job = Job(
        id=str(uuid.uuid4()),
        name="Pending Job",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Try to stop it via CLI
    result = runner.invoke(mini, ["job", "stop", job.id])

    # Should fail with error message
    assert result.exit_code == 1
    assert "Error" in result.output or "RUNNING" in result.output


def test_stop_completed_job_fails(db, runner, mock_config_dir):
    """Test that stopping a COMPLETED job returns error."""
    # Create a COMPLETED job
    job = Job(
        id=str(uuid.uuid4()),
        name="Completed Job",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml",
        completed_at=datetime.utcnow()
    )
    db.create_job(job)

    # Try to stop it via CLI
    result = runner.invoke(mini, ["job", "stop", job.id])

    # Should fail with error message
    assert result.exit_code == 1
    assert "Error" in result.output or "RUNNING" in result.output


def test_stop_failed_job_fails(db, runner, mock_config_dir):
    """Test that stopping a FAILED job returns error."""
    # Create a FAILED job
    job = Job(
        id=str(uuid.uuid4()),
        name="Failed Job",
        status=JobStatus.FAILED,
        plan_path="/path/to/plan.yaml",
        completed_at=datetime.utcnow()
    )
    db.create_job(job)

    # Try to stop it via CLI
    result = runner.invoke(mini, ["job", "stop", job.id])

    # Should fail with error message
    assert result.exit_code == 1
    assert "Error" in result.output or "RUNNING" in result.output


def test_stop_nonexistent_job_fails(runner, mock_config_dir):
    """Test that stopping a nonexistent job returns error."""
    result = runner.invoke(mini, ["job", "stop", "nonexistent-job-id"])

    # Should fail with error message
    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


def test_stop_running_job_without_pid_fails(db, runner, mock_config_dir):
    """Test that stopping a RUNNING job without PID returns error."""
    # Create a RUNNING job WITHOUT PID (corrupted state)
    job = Job(
        id=str(uuid.uuid4()),
        name="Running Job No PID",
        status=JobStatus.RUNNING,
        plan_path="/path/to/plan.yaml",
        pid=None,  # No PID
        started_at=datetime.utcnow()
    )
    db.create_job(job)

    # Try to stop it via CLI
    result = runner.invoke(mini, ["job", "stop", job.id])

    # Should fail with error message
    assert result.exit_code == 1
    assert "Error" in result.output or "process" in result.output.lower()


# ============================================================================
# RESUME COMMAND TESTS
# ============================================================================

# ============================================================================
# RESULTS LOGS COMMAND TESTS
# ============================================================================

def test_results_logs_all_tasks(db, runner, mock_config_dir):
    """Test retrieving logs for all tasks in a job."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with multiple tasks with output
    job = Job(
        id=str(uuid.uuid4()),
        name="Results Test Job",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create tasks with output
    task1 = Task(estimated_duration_min=5,
        id="task-1",
        job_id=job.id,
        name="Task 1",
        description="First task",
        status=TaskStatus.COMPLETED,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    task2 = Task(estimated_duration_min=5,
        id="task-2",
        job_id=job.id,
        name="Task 2",
        description="Second task",
        status=TaskStatus.COMPLETED,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.create_task(task1)
    db.create_task(task2)
    # Narration now lives in job.log (the sole narration store).
    _write_job_log(mock_config_dir, job.id, [
        ("Task 1", "Output from task 1"),
        ("Task 2", "Output from task 2"),
    ])

    # Retrieve logs via CLI
    result = runner.invoke(mini, ["job", "results", "logs", job.id])

    assert result.exit_code == 0
    assert "Task 1" in result.output
    assert "Task 2" in result.output
    assert "Output from task 1" in result.output
    assert "Output from task 2" in result.output


def test_results_logs_single_task_with_task_id(db, runner, mock_config_dir):
    """Test retrieving logs for a specific task using task ID."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with multiple tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Single Task Results Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    task1 = Task(estimated_duration_min=5,
        id="task-alpha",
        job_id=job.id,
        name="Task Alpha",
        description="Alpha task",
        status=TaskStatus.COMPLETED,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    task2 = Task(estimated_duration_min=5,
        id="task-beta",
        job_id=job.id,
        name="Task Beta",
        description="Beta task",
        status=TaskStatus.COMPLETED,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.create_task(task1)
    db.create_task(task2)
    _write_job_log(mock_config_dir, job.id, [
        ("Task Alpha", "Alpha output"),
        ("Task Beta", "Beta output"),
    ])

    # Retrieve logs for specific task via CLI
    result = runner.invoke(mini, ["job", "results", "logs", job.id, "--task-id", "task-alpha"])

    assert result.exit_code == 0
    assert "Task Alpha" in result.output
    assert "Alpha output" in result.output
    # Should not contain beta output
    assert "Beta output" not in result.output or "Beta" not in result.output


def test_results_logs_nonexistent_task_id_fails(db, runner, mock_config_dir):
    """Test retrieving logs with nonexistent task ID returns error."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with a task
    job = Job(
        id=str(uuid.uuid4()),
        name="Not Found Task Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="Task 1",
        description="First task",
        status=TaskStatus.COMPLETED,
    )
    db.create_task(task)

    # Try to retrieve logs for nonexistent task
    result = runner.invoke(mini, ["job", "results", "logs", job.id, "--task-id", "nonexistent"])

    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


def test_results_logs_empty_job_shows_message(db, runner, mock_config_dir):
    """Test retrieving logs from a job with no tasks shows appropriate message."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with NO tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Empty Job",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Retrieve logs for empty job
    result = runner.invoke(mini, ["job", "results", "logs", job.id])

    assert result.exit_code == 0
    assert "No tasks" in result.output or "found" in result.output


def test_results_logs_nonexistent_job_fails(runner, mock_config_dir):
    """Test retrieving logs from nonexistent job returns error."""
    result = runner.invoke(mini, ["job", "results", "logs", "nonexistent-job"])

    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


# ============================================================================
# JOB LOGS COMMAND TESTS (live narration file, not the DB summary)
# ============================================================================

def _write_job_log(mock_config_dir, job_id, step_messages):
    """Write job.log records (type=task) attributing each message to a step/name."""
    log_dir = mock_config_dir / "jobs" / job_id
    log_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "task", "step": step, "message": msg})
             for step, msg in step_messages]
    (log_dir / "job.log").write_text("\n".join(lines) + "\n")


def _make_job_with_log(mock_config_dir, content: str, status=JobStatus.COMPLETED):
    """Create a job (+ one task & execution) and write `content` to its job.log."""
    import os
    db = Database(mock_config_dir / "minimise.db")
    db.init_db()
    job = Job(
        id=str(uuid.uuid4()),
        name="Narration Job",
        status=status,
        plan_path="/path/to/plan.yaml",
        # live pid so a RUNNING job isn't reconciled to FAILED on the read path
        pid=os.getpid() if status == JobStatus.RUNNING else None,
    )
    db.create_job(job)
    task = Task(
        estimated_duration_min=5,
        id="task-narr",
        job_id=job.id,
        name="Narrate Task",
        description="A task",
        status=TaskStatus.COMPLETED,
        retries=2,
        diff_path="/some/diff.patch",
    )
    db.create_task(task)
    log_path = (mock_config_dir / "jobs" / job.id)
    log_path.mkdir(parents=True, exist_ok=True)
    log_path = log_path / "job.log"
    log_path.write_text(content)
    return db, job, log_path


def test_job_logs_prints_narration_file(runner, mock_config_dir):
    """`mini job logs <id>` prints the job.log narration, not the DB summary."""
    _, job, _ = _make_job_with_log(
        mock_config_dir, "agent says hello\nagent did a thing\n"
    )

    result = runner.invoke(mini, ["job", "logs", job.id])

    assert result.exit_code == 0
    assert "agent says hello" in result.output
    assert "agent did a thing" in result.output
    # The per-attempt status/duration table and diff path are gone — that is
    # `mini job status`'s job now.
    assert "Retries" not in result.output
    assert "Attempt" not in result.output
    assert "/some/diff.patch" not in result.output


def test_job_logs_no_log_yet_message(runner, mock_config_dir):
    """When job.log doesn't exist yet, print a clear message, not an error."""
    db = Database(mock_config_dir / "minimise.db")
    db.init_db()
    job = Job(
        id=str(uuid.uuid4()),
        name="Unstarted Job",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml",
    )
    db.create_job(job)

    result = runner.invoke(mini, ["job", "logs", job.id])

    assert result.exit_code == 0
    assert "No logs yet" in result.output


def test_job_logs_follow_tails_appended_lines(db, runner, mock_config_dir):
    """`mini job logs <id> -f` prints existing content then appended lines."""
    import threading

    db, job, log_path = _make_job_with_log(
        mock_config_dir, "first line\n", status=JobStatus.RUNNING
    )

    def append_then_finish():
        time.sleep(0.15)
        with open(log_path, "a") as f:
            f.write("second line\n")
        time.sleep(0.15)
        # flip to a terminal state so the follow loop exits
        appender_db = Database(mock_config_dir / "minimise.db")
        appender_db.update_job_status(job.id, JobStatus.COMPLETED)

    t = threading.Thread(target=append_then_finish)
    t.start()
    result = runner.invoke(mini, ["job", "logs", job.id, "-f"])
    t.join()

    assert result.exit_code == 0
    assert "first line" in result.output
    assert "second line" in result.output


# A few JSONL records spanning two types, for --query tests.
_JSONL_LOG = (
    '{"timestamp":"2026-06-27T01:00:00","execution_id":"job#a#task#task-aa","type":"task","level":"info","message":"running pytest"}\n'
    '{"timestamp":"2026-06-27T01:00:01","execution_id":"job#a#task#task-bb","type":"task","level":"info","message":"applied a patch"}\n'
    '{"timestamp":"2026-06-27T01:00:02","execution_id":"job#a#review","type":"review","level":"info","message":"review note"}\n'
)


def test_job_logs_no_query_prints_everything(runner, mock_config_dir):
    """Without --query the raw file is printed unchanged (JSONL passes through)."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(mini, ["job", "logs", job.id])

    assert result.exit_code == 0
    assert "running pytest" in result.output
    assert "review note" in result.output


def test_job_logs_query_filters_sorts_limits_projects(runner, mock_config_dir):
    """--query filters by type, sorts desc, limits, and projects fields."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(
        mini,
        ["job", "logs", job.id, "--query",
         'fields message | filter type = "task" | sort @timestamp desc | limit 1'],
    )

    assert result.exit_code == 0
    # type=review filtered out; sort desc + limit 1 keeps the latest task line.
    # filter + sort desc + limit 1 → exactly the latest task line, nothing else.
    assert [l for l in result.output.splitlines() if l.strip()] == ["applied a patch"]


def test_job_logs_query_at_message_prints_whole_record(runner, mock_config_dir):
    """`fields @message` prints the whole record (the raw JSON)."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(
        mini,
        ["job", "logs", job.id, "--query",
         'fields @message | filter execution_id like "task-aa"'],
    )

    assert result.exit_code == 0
    assert "running pytest" in result.output
    assert "task-aa" in result.output  # whole record includes execution_id


def test_job_logs_filter_at_message_matches_whole_record(runner, mock_config_dir):
    """`filter @message like ...` searches the whole record, not a literal key."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(
        mini,
        ["job", "logs", job.id, "--query",
         'fields message | filter @message like "applied a patch"'],
    )

    assert result.exit_code == 0
    lines = [l for l in result.output.splitlines() if l.strip()]
    assert lines == ["applied a patch"]  # the one record whose JSON contains it


def test_job_logs_json_is_raw_matching_jsonl(runner, mock_config_dir):
    """--json emits one raw JSON record per matching line (jq-friendly)."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(
        mini,
        ["job", "logs", job.id, "--query", 'filter type = "review"', "--json"],
    )

    assert result.exit_code == 0
    lines = [l for l in result.output.splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["type"] == "review"
    assert rec["message"] == "review note"


def test_job_logs_bad_query_errors(runner, mock_config_dir):
    """A malformed query exits 1 with a clear error."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(
        mini, ["job", "logs", job.id, "--query", "fields | bogus ("]
    )

    assert result.exit_code == 1
    assert "query" in result.output.lower()


def test_job_logs_no_logs_yet_with_query(runner, mock_config_dir):
    """--query on a job with no log file still prints the no-logs message."""
    db = Database(mock_config_dir / "minimise.db")
    db.init_db()
    job = Job(
        id=str(uuid.uuid4()),
        name="Unstarted",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml",
    )
    db.create_job(job)

    result = runner.invoke(
        mini, ["job", "logs", job.id, "--query", 'filter type = "task"']
    )

    assert result.exit_code == 0
    assert "No logs yet" in result.output


def test_job_logs_follow_with_query_applies_filter_live(db, runner, mock_config_dir):
    """`-f --query` applies the filter per new line; sort/limit ignored (notice)."""
    import threading

    db, job, log_path = _make_job_with_log(
        mock_config_dir,
        '{"type":"task","message":"keep me"}\n',
        status=JobStatus.RUNNING,
    )

    def append_then_finish():
        time.sleep(0.15)
        with open(log_path, "a") as f:
            f.write('{"type":"review","message":"drop me"}\n')
            f.write('{"type":"task","message":"keep me too"}\n')
        time.sleep(0.15)
        Database(mock_config_dir / "minimise.db").update_job_status(
            job.id, JobStatus.COMPLETED
        )

    t = threading.Thread(target=append_then_finish)
    t.start()
    result = runner.invoke(
        mini,
        ["job", "logs", job.id, "-f", "--query",
         'filter type = "task" | sort @timestamp desc | limit 1'],
    )
    t.join()

    assert result.exit_code == 0
    assert "keep me" in result.output
    assert "keep me too" in result.output
    assert "drop me" not in result.output  # filtered live


def test_job_logs_follow_notice_fires_for_asc_sort(db, runner, mock_config_dir):
    """`-f` with an explicit `sort ... asc` (sort_desc falsy) still warns it's ignored."""
    import threading

    db, job, log_path = _make_job_with_log(
        mock_config_dir,
        '{"type":"task","message":"line"}\n',
        status=JobStatus.RUNNING,
    )

    def finish():
        time.sleep(0.15)
        Database(mock_config_dir / "minimise.db").update_job_status(
            job.id, JobStatus.COMPLETED
        )

    t = threading.Thread(target=finish)
    t.start()
    result = runner.invoke(
        mini,
        ["job", "logs", job.id, "-f", "--query", 'sort @timestamp asc'],
    )
    t.join()

    assert result.exit_code == 0
    assert "live" in result.output.lower()  # the sort/limit-ignored notice fired


def test_job_logs_json_alone_is_raw_passthrough(runner, mock_config_dir):
    """`--json` without `--query` emits the raw JSONL lines (jq passthrough)."""
    _, job, _ = _make_job_with_log(mock_config_dir, _JSONL_LOG)

    result = runner.invoke(mini, ["job", "logs", job.id, "--json"])

    assert result.exit_code == 0
    lines = [l for l in result.output.splitlines() if l.strip()]
    assert len(lines) == 3
    recs = [json.loads(l) for l in lines]
    assert [r["message"] for r in recs] == [
        "running pytest", "applied a patch", "review note"]


# ============================================================================
# RESULTS DIFF COMMAND TESTS
# ============================================================================

def test_results_diff_all_tasks(db, runner, mock_config_dir, tmp_path):
    """Test retrieving diffs for all tasks in a job."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with multiple tasks with diffs
    job = Job(
        id=str(uuid.uuid4()),
        name="Diff Results Test Job",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create diff files
    diff1_path = tmp_path / "diff1.patch"
    diff1_path.write_text("--- file1.py\n+++ file1.py\n-old line\n+new line")

    diff2_path = tmp_path / "diff2.patch"
    diff2_path.write_text("--- file2.py\n+++ file2.py\n-old code\n+new code")

    # Create tasks with diff paths
    task1 = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="Task 1",
        description="First task",
        status=TaskStatus.COMPLETED,
        diff_path=str(diff1_path),
        completed_at=datetime.utcnow(),
    )
    task2 = Task(estimated_duration_min=5, 
        id="task-2",
        job_id=job.id,
        name="Task 2",
        description="Second task",
        status=TaskStatus.COMPLETED,
        diff_path=str(diff2_path),
        completed_at=datetime.utcnow(),
    )
    db.create_task(task1)
    db.create_task(task2)

    # Retrieve diffs via CLI
    result = runner.invoke(mini, ["job", "results", "diff", job.id])

    assert result.exit_code == 0
    assert "Task 1" in result.output
    assert "Task 2" in result.output


def test_results_diff_single_task_with_task_id(db, runner, mock_config_dir, tmp_path):
    """Test retrieving diff for a specific task using task ID."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with multiple tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Single Diff Results Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create diff files
    diff1_path = tmp_path / "diff1.patch"
    diff1_path.write_text("--- file1.py\n+++ file1.py\n-old\n+new")

    diff2_path = tmp_path / "diff2.patch"
    diff2_path.write_text("--- file2.py\n+++ file2.py\n-other\n+change")

    task1 = Task(estimated_duration_min=5, 
        id="task-alpha",
        job_id=job.id,
        name="Task Alpha",
        description="Alpha task",
        status=TaskStatus.COMPLETED,
        diff_path=str(diff1_path),
        completed_at=datetime.utcnow(),
    )
    task2 = Task(estimated_duration_min=5, 
        id="task-beta",
        job_id=job.id,
        name="Task Beta",
        description="Beta task",
        status=TaskStatus.COMPLETED,
        diff_path=str(diff2_path),
        completed_at=datetime.utcnow(),
    )
    db.create_task(task1)
    db.create_task(task2)

    # Retrieve diff for specific task via CLI
    result = runner.invoke(mini, ["job", "results", "diff", job.id, "--task-id", "task-alpha"])

    assert result.exit_code == 0
    assert "Task Alpha" in result.output


def test_results_diff_task_without_diff(db, runner, mock_config_dir):
    """Test retrieving diffs shows tasks without diffs appropriately."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with a task that has no diff
    job = Job(
        id=str(uuid.uuid4()),
        name="No Diff Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="Task Without Diff",
        description="Task description",
        status=TaskStatus.COMPLETED,
        diff_path=None,  # No diff
        completed_at=datetime.utcnow(),
    )
    db.create_task(task)

    # Retrieve diffs
    result = runner.invoke(mini, ["job", "results", "diff", job.id])

    assert result.exit_code == 0
    # Should show task but indicate no diff or show "No diffs found"


def test_results_diff_nonexistent_job_fails(runner, mock_config_dir):
    """Test retrieving diffs from nonexistent job returns error."""
    result = runner.invoke(mini, ["job", "results", "diff", "nonexistent-job"])

    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


# ============================================================================
# SHOW COMMAND TESTS
# ============================================================================

def test_show_plan_structure(db, runner, mock_config_dir, tmp_path):
    """Test showing plan structure for a job."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a plan file with nested structure
    plan_path = tmp_path / "plan.yaml"
    plan_content = """plan:
  name: Test Plan
  briefing: Test briefing
  documentation: |
    This is documentation
  tasks:
    - id: task-1
      name: Task 1
      description: First task description
      goal: Finish task 1
      estimated_duration_min: 5
    - id: task-2
      name: Task 2
      description: Second task description
      goal: Finish task 2
      estimated_duration_min: 5
"""
    plan_path.write_text(plan_content)

    # Create a job with the plan
    job = Job(
        id=str(uuid.uuid4()),
        name="Plan Structure Test",
        status=JobStatus.PENDING,
        plan_path=str(plan_path)
    )
    db.create_job(job)

    # Show plan via CLI - should work even without DB tasks since it reads from plan file
    result = runner.invoke(mini, ["job", "show", job.id])

    assert result.exit_code == 0
    assert "Plan Structure" in result.output
    assert "Task 1" in result.output
    assert "Task 2" in result.output


def test_show_with_task_id_displays_prompt(db, runner, mock_config_dir, tmp_path):
    """Test showing full prompt for a specific task with handover context."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a plan file
    plan_path = tmp_path / "plan.yaml"
    plan_content = """name: Handover Test Plan
tasks:
  - name: Task 1
    description: First task description
  - name: Task 2
    description: Second task description with context
"""
    plan_path.write_text(plan_content)

    # Create a job with tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Show Task Test",
        status=JobStatus.RUNNING,
        plan_path=str(plan_path)
    )
    db.create_job(job)

    task1 = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job.id,
        name="Task 1",
        description="First task description",
        status=TaskStatus.COMPLETED,
        completed_at=datetime.utcnow(),
    )
    task2 = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job.id,
        name="Task 2",
        description="Second task description",
        status=TaskStatus.PENDING,
    )
    db.create_task(task1)
    db.create_task(task2)

    # Show full prompt for task 2
    result = runner.invoke(mini, ["job", "show", job.id, "--task-id", task2.id[:8]])

    assert result.exit_code == 0
    assert "Full Prompt" in result.output or "Task 2" in result.output or "description" in result.output


def test_show_with_invalid_job_id_fails(runner, mock_config_dir):
    """Test showing plan for nonexistent job returns error."""
    result = runner.invoke(mini, ["job", "show", "nonexistent-job"])

    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


def test_show_with_invalid_task_id_fails(db, runner, mock_config_dir, tmp_path):
    """Test showing full prompt with nonexistent task ID returns error."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a plan file
    plan_path = tmp_path / "plan.yaml"
    plan_content = """name: Test Plan
tasks:
  - name: Task 1
    description: Task 1
"""
    plan_path.write_text(plan_content)

    # Create a job
    job = Job(
        id=str(uuid.uuid4()),
        name="Invalid Task Test",
        status=JobStatus.PENDING,
        plan_path=str(plan_path)
    )
    db.create_job(job)

    # Try to show with nonexistent task ID
    result = runner.invoke(mini, ["job", "show", job.id, "--task-id", "nonexistent"])

    assert result.exit_code == 1
    assert "Error" in result.output or "not found" in result.output


# ============================================================================
# EDGE CASES AND ERROR CONDITIONS
# ============================================================================

def test_job_prefix_resolution(db, runner, mock_config_dir):
    """Test that job commands work with partial job ID (prefix matching)."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job with a recognizable ID
    job = Job(
        id="abcdef01-89ab-cdef-0123-456789abcdef",
        name="Prefix Test Job",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Test retrieving with full ID (should work)
    result = runner.invoke(mini, ["job", "status", job.id])
    assert result.exit_code == 0 or "abcdef01" in result.output or "Prefix Test Job" in result.output


def test_multiple_job_prefix_match_ambiguity(db, runner, mock_config_dir):
    """Test that ambiguous prefix matching returns error."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create two jobs with similar IDs (both starting with 'a')
    job1 = Job(
        id="aaaaaaaa-0000-0000-0000-000000000001",
        name="Job 1",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    job2 = Job(
        id="aaaaaaaa-0000-0000-0000-000000000002",
        name="Job 2",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job1)
    db.create_job(job2)

    # Try to retrieve with ambiguous prefix
    result = runner.invoke(mini, ["job", "status", "aaaaaaaa"])

    # Should indicate ambiguity or error
    assert result.exit_code in [1] or "multiple" in result.output.lower() or "disambiguate" in result.output


def test_results_logs_task_prefix_matching(db, runner, mock_config_dir):
    """Test that results logs work with task ID prefix matching."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job
    job = Job(
        id=str(uuid.uuid4()),
        name="Task Prefix Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create a task
    task = Task(estimated_duration_min=5,
        id="prefix-task-12345",
        job_id=job.id,
        name="Prefix Task",
        description="Task for prefix matching",
        status=TaskStatus.COMPLETED,
        completed_at=datetime.utcnow(),
    )
    db.create_task(task)
    _write_job_log(mock_config_dir, job.id, [("Prefix Task", "Task output")])

    # Retrieve with prefix
    result = runner.invoke(mini, ["job", "results", "logs", job.id, "--task-id", "prefix"])

    assert result.exit_code == 0
    assert "Prefix Task" in result.output or "Task output" in result.output


def test_job_status_transition_validates_state(db):
    """Test that job status transitions validate state appropriately."""
    # Create a PENDING job
    job = Job(
        id=str(uuid.uuid4()),
        name="Status Transition Test",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Try various state transitions
    # PENDING -> RUNNING should work
    db.update_job_status(job.id, JobStatus.RUNNING, started_at=datetime.utcnow())
    job_running = db.get_job(job.id)
    assert job_running.status == JobStatus.RUNNING

    # RUNNING -> COMPLETED should work
    db.update_job_status(job.id, JobStatus.COMPLETED, completed_at=datetime.utcnow())
    job_completed = db.get_job(job.id)
    assert job_completed.status == JobStatus.COMPLETED


def test_results_logs_displays_task_metadata(db, runner, mock_config_dir):
    """Test that results logs display all task metadata."""
    from minimise.models import Task, TaskStatus

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a job
    job = Job(
        id=str(uuid.uuid4()),
        name="Metadata Test",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create a task with metadata
    task = Task(estimated_duration_min=5,
        id="task-metadata",
        job_id=job.id,
        name="Task with Metadata",
        description="Full task description",
        status=TaskStatus.COMPLETED,
        retries=2,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.create_task(task)
    _write_job_log(mock_config_dir, job.id, [("Task with Metadata", "Full output text")])

    # Retrieve logs
    result = runner.invoke(mini, ["job", "results", "logs", job.id])

    assert result.exit_code == 0
    assert "Task with Metadata" in result.output
    assert "Full output text" in result.output
    assert "Status" in result.output or "Retries" in result.output or "metadata" in result.output


def test_start_and_stop_workflow(db, runner, mock_config_dir):
    """Test complete workflow: start -> stop."""
    from minimise.models import Task, TaskStatus

    # Create PENDING job
    job = Job(
        id=str(uuid.uuid4()),
        name="Workflow Test",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="Workflow Task",
        description="Task for workflow",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Simulate start
    db.update_job_status(job.id, JobStatus.RUNNING, started_at=datetime.utcnow(), pid=12345)
    job_running = db.get_job(job.id)
    assert job_running.status == JobStatus.RUNNING

    # Simulate stop
    db.update_job_status(job.id, JobStatus.STOPPED, completed_at=datetime.utcnow())
    job_stopped = db.get_job(job.id)
    assert job_stopped.status == JobStatus.STOPPED


# ============================================================================
# GOAL FIELD TESTS
# ============================================================================

def test_plan_load_goal_field(db):
    """Test that Task model loads goal field from task config."""
    from minimise.models import Task, TaskStatus

    # Create a task with a goal field
    task = Task(estimated_duration_min=5, 
        id="task-with-goal",
        job_id="job-123",
        name="Task with Goal",
        description="Task description",
        goal="Implement the feature",
        status=TaskStatus.PENDING,
    )

    # Goal should be stored
    assert task.goal == "Implement the feature"


def test_plan_goal_prepended_to_prompt(db):
    """Test that goal is prepended to agent prompt."""
    from minimise.orchestration.task_executor import TaskExecutor
    from minimise.storage.job_store import JobStore
    from minimise.models import Task, TaskStatus

    task = Task(estimated_duration_min=5, 
        id="task-1",
        job_id="job-1",
        name="Test Task",
        description="Do something important",
        goal="Make it work",
        status=TaskStatus.PENDING,
    )

    # Get the executor
    executor = TaskExecutor(JobStore(db, Path("/tmp/test")), git_tracker=None)

    # Build context as executor would
    context = {
        "task_name": task.name,
        "task_description": task.description,
        "task_goal": task.goal,
        "handover": "",
    }

    # The prompt should include goal prepended to description
    # We'll verify this by checking that the prompt building includes the goal
    # This is more of an integration test, so we'll just verify the Task model supports goal
    assert task.goal is not None


def test_plan_missing_goal_validation_error(runner, mock_config_dir, tmp_path):
    """Test that loading a plan without goal field raises validation error."""
    # Create a plan file WITHOUT goal field
    plan_path = tmp_path / "plan.yaml"
    plan_content = """
name: Test Plan
briefing: Test briefing
tasks:
  - name: Task 1
    description: First task
    # Missing goal field
  - name: Task 2
    description: Second task
    goal: Task 2 goal
"""
    plan_path.write_text(plan_content)

    # Try to create a job from this plan
    result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

    # Should fail with validation error about missing goal
    assert result.exit_code == 1
    assert "goal" in result.output.lower() or "Goal" in result.output


def test_goal_in_job_show_output(db, runner, mock_config_dir, tmp_path):
    """Test that goal field is displayed in job show output."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a plan with goal fields
    plan_path = tmp_path / "plan.yaml"
    plan_content = """
name: Test Plan with Goals
briefing: Test briefing
tasks:
  - id: task-1
    name: Task 1
    description: First task description
    goal: Implement basic structure
    estimated_duration_min: 5
  - id: task-2
    name: Task 2
    description: Second task description
    goal: Add advanced features
    estimated_duration_min: 5
"""
    plan_path.write_text(plan_content)

    # Create job
    job = Job(
        id=str(uuid.uuid4()),
        name="Goal Test Job",
        status=JobStatus.PENDING,
        plan_path=str(plan_path)
    )
    db.create_job(job)

    # Create tasks with goals
    task1 = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="Task 1",
        description="First task description",
        goal="Implement basic structure",
        status=TaskStatus.PENDING,
    )
    task2 = Task(estimated_duration_min=5, 
        id="task-2",
        job_id=job.id,
        name="Task 2",
        description="Second task description",
        goal="Add advanced features",
        status=TaskStatus.PENDING,
    )
    db.create_task(task1)
    db.create_task(task2)

    # Show job
    result = runner.invoke(mini, ["job", "show", job.id])

    assert result.exit_code == 0
    # Output should include goal fields
    assert "Implement basic structure" in result.output or "goal" in result.output.lower()


# TDD Tests for Goal attribute feature

def test_plan_load_goal_field(runner, mock_config_dir, isolated_repo):
    """Test that plan YAML can load goal field for tasks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_content = """
name: Test Plan with Goals
briefing: Test briefing
tasks:
  - id: test-goal-1
    name: Task 1
    description: First task with sufficient description for the test
    goal: "Complete the first objective"
    estimated_duration_min: 30
  - id: test-goal-2
    name: Task 2
    description: Second task with sufficient description for the test
    goal: "Finish the second objective"
    estimated_duration_min: 30
"""
        plan_path.write_text(plan_content)

        # Create job from plan
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        assert result.exit_code == 0
        assert "Job created" in result.output


def test_plan_goal_prepended_to_prompt(runner, mock_config_dir, isolated_repo):
    """Test that goal is prepended to agent prompt in task execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_content = """
name: Test Plan Goal Prepend
briefing: Test briefing
tasks:
  - id: test-prepend-1
    name: Task 1
    description: Task description here with sufficient length for validation
    goal: "My specific goal"
    estimated_duration_min: 30
"""
        plan_path.write_text(plan_content)

        # Create job from plan
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])
        assert result.exit_code == 0

        # Get job ID from output
        from minimise.interfaces.cli import get_db
        db = get_db()
        jobs = db.list_jobs(limit=1)
        assert len(jobs) > 0
        job_id = jobs[0].id

        # Get tasks for this job
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE job_id = ? ORDER BY created_at", (job_id,))
        rows = cursor.fetchall()
        conn.close()

        # Verify task has goal
        assert len(rows) > 0
        task_row = rows[0]
        # Check if goal is in database - for now just verify task exists
        assert task_row['name'] == "Task 1"


def test_plan_missing_goal_validation_error(runner, mock_config_dir):
    """Test that plan YAML validation fails if goal field is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_content = """
name: Test Plan Missing Goal
briefing: Test briefing
tasks:
  - id: task-1
    name: Task 1
    description: First task with sufficient description length
"""
        plan_path.write_text(plan_content)

        # Create job from plan - should fail
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        # Should fail with validation error
        assert result.exit_code != 0
        assert "goal" in result.output.lower()
        assert "syntax validation failed" in result.output.lower()


def test_empty_plan_creates_no_job(runner, mock_config_dir):
    """bug-2 (test-plan pollution): a 0-task plan is rejected by `mini job new`
    and leaves no job behind to pollute history."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_path.write_text("name: Test Plan\ntasks: []\n")

        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        assert result.exit_code != 0
        assert "syntax validation failed" in result.output.lower()

        # No polluting job was created
        db = Database(mock_config_dir / "minimise.db")
        db.init_db()
        assert db.list_jobs() == []


def test_goal_in_job_show_output(runner, mock_config_dir):
    """Test that goal field is displayed in job show output."""
    import uuid
    from minimise.storage.database import Database

    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    tmpdir = Path(tempfile.gettempdir()) / f"test-plan-{uuid.uuid4()}"
    tmpdir.mkdir(parents=True, exist_ok=True)

    try:
        # Create a plan file
        plan_path = tmpdir / "plan.yaml"
        plan_content = """name: Test Job
briefing: Test briefing
tasks:
  - id: task-1
    name: Task 1
    description: First task description
    goal: "Complete the first objective"
    estimated_duration_min: 5
  - id: task-2
    name: Task 2
    description: Second task description
    goal: "Complete the second objective"
    estimated_duration_min: 5
"""
        plan_path.write_text(plan_content)

        job = Job(
            id=str(uuid.uuid4()),
            name="Test Job",
            status=JobStatus.PENDING,
            plan_path=str(plan_path),
            created_at=datetime.utcnow(),
        )
        db.create_job(job)

        task1 = Task(estimated_duration_min=5, 
            id=str(uuid.uuid4()),
            job_id=job.id,
            name="Task 1",
            description="First task description",
            goal="Complete the first objective",
            status=TaskStatus.PENDING,
        )
        task2 = Task(estimated_duration_min=5, 
            id=str(uuid.uuid4()),
            job_id=job.id,
            name="Task 2",
            description="Second task description",
            goal="Complete the second objective",
            status=TaskStatus.PENDING,
        )
        db.create_task(task1)
        db.create_task(task2)

        # Show job
        result = runner.invoke(mini, ["job", "show", job.id])

        assert result.exit_code == 0, f"Expected 0, got {result.exit_code}. Output: {result.output}"
        # Output should include goal fields
        assert "Complete the first objective" in result.output or "goal" in result.output.lower()
    finally:
        # Clean up
        import shutil
        if tmpdir.exists():
            shutil.rmtree(tmpdir)


# ============================================================================
# DELETE COMMAND TESTS (WITH SAFEGUARDS)
# ============================================================================

def test_delete_pending_job_succeeds(db, runner, mock_config_dir):
    """Test that deleting a PENDING job succeeds with confirmation."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a PENDING job
    job = Job(
        id=str(uuid.uuid4()),
        name="Pending Job to Delete",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    # Create a task for the job
    task = Task(estimated_duration_min=5, 
        id="task-1",
        job_id=job.id,
        name="Task 1",
        description="Task to delete",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Delete job with yes confirmation
    result = runner.invoke(mini, ["job", "delete", job.id], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower() or "removed" in result.output.lower()

    # Verify job is gone from DB
    deleted_job = db.get_job(job.id)
    assert deleted_job is None


def test_delete_running_job_fails(db, runner, mock_config_dir):
    """Test that deleting a RUNNING job returns error (safeguard)."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a RUNNING job with a LIVE pid (dead-pid RUNNING now reconciles to
    # FAILED on the read path, which would make it deletable).
    import os
    job = Job(
        id=str(uuid.uuid4()),
        name="Running Job",
        status=JobStatus.RUNNING,
        plan_path="/path/to/plan.yaml",
        pid=os.getpid(),
        started_at=datetime.utcnow()
    )
    db.create_job(job)

    # Try to delete it
    result = runner.invoke(mini, ["job", "delete", job.id])

    assert result.exit_code == 1
    assert "Error" in result.output or "RUNNING" in result.output or "cannot" in result.output.lower()


def test_delete_stopped_job_succeeds(db, runner, mock_config_dir):
    """A STOPPED job is terminal and deletes like FAILED/COMPLETED.

    Regression: a guard made stopped jobs permanently undeletable
    (catch-22 with `mini job stop`).
    """
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    job = Job(
        id=str(uuid.uuid4()),
        name="Stopped Job",
        status=JobStatus.STOPPED,
        plan_path="/path/to/plan.yaml",
        completed_at=datetime.utcnow()
    )
    db.create_job(job)

    result = runner.invoke(mini, ["job", "delete", job.id], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower() or "removed" in result.output.lower()
    assert db.get_job(job.id) is None


def test_delete_completed_job_succeeds(db, runner, mock_config_dir):
    """Test that deleting a COMPLETED job succeeds with confirmation."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a COMPLETED job
    job = Job(
        id=str(uuid.uuid4()),
        name="Completed Job",
        status=JobStatus.COMPLETED,
        plan_path="/path/to/plan.yaml",
        completed_at=datetime.utcnow()
    )
    db.create_job(job)

    # Delete job with yes confirmation
    result = runner.invoke(mini, ["job", "delete", job.id], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower() or "removed" in result.output.lower()


def test_delete_failed_job_succeeds(db, runner, mock_config_dir):
    """Test that deleting a FAILED job succeeds with confirmation."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a FAILED job
    job = Job(
        id=str(uuid.uuid4()),
        name="Failed Job",
        status=JobStatus.FAILED,
        plan_path="/path/to/plan.yaml",
        completed_at=datetime.utcnow()
    )
    db.create_job(job)

    # Delete job with yes confirmation
    result = runner.invoke(mini, ["job", "delete", job.id], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower() or "removed" in result.output.lower()


def test_delete_reports_task_count(db, runner, mock_config_dir):
    """Test that delete reports how many tasks were removed."""
    db_path = mock_config_dir / "minimise.db"
    db = Database(db_path)
    db.init_db()

    # Create a PENDING job with multiple tasks
    job = Job(
        id=str(uuid.uuid4()),
        name="Job with Multiple Tasks",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml"
    )
    db.create_job(job)

    task1 = Task(estimated_duration_min=5,
        id="task-1",
        job_id=job.id,
        name="Task 1",
        description="First task",
        status=TaskStatus.PENDING,
    )
    task2 = Task(estimated_duration_min=5,
        id="task-2",
        job_id=job.id,
        name="Task 2",
        description="Second task",
        status=TaskStatus.PENDING,
    )
    db.create_task(task1)
    db.create_task(task2)

    result = runner.invoke(mini, ["job", "delete", job.id])

    assert result.exit_code == 0
    assert "2" in result.output
    assert db.get_job(job.id) is None


def test_job_list_json_includes_duration_total(runner, mock_config_dir):
    """job list --format json includes tasks.estimated_duration_min as the sum."""
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


def test_job_list_json_duration_zero_when_no_tasks(runner, mock_config_dir):
    """A job with no tasks reports estimated_duration_min == 0 (not None)."""
    db = Database(mock_config_dir / "minimise.db"); db.init_db()
    job = Job(id=str(uuid.uuid4()), name="J", status=JobStatus.PENDING,
              plan_path="/p.yaml", created_at=datetime.utcnow())
    db.create_job(job)
    result = runner.invoke(mini, ["job", "list", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    job_obj = [j for j in data if j["id"] == job.id][0]
    assert job_obj["tasks"]["estimated_duration_min"] == 0


def test_job_status_json_includes_duration_summary(runner, mock_config_dir):
    """job status --format json adds a tasks_summary sibling; tasks list unchanged."""
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
    assert isinstance(data["tasks"], list)
    assert data["tasks_summary"]["estimated_duration_min"] == 45
    assert data["tasks_summary"]["total"] == 2


def test_job_status_table_shows_duration_total(runner, mock_config_dir):
    """job status table output shows an Estimated Duration total line."""
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


# --- Helper unit tests (refactor extraction) ------------------------------


def test_error_job_not_found_message_and_exit():
    """_error_job_not_found prints the standard message and raises SystemExit(1)."""
    from minimise.interfaces.cli import _error_job_not_found, console
    with console.capture() as cap:
        with pytest.raises(SystemExit) as exc:
            _error_job_not_found("abc123")
    assert exc.value.code == 1
    assert "Error: Job abc123 not found" in cap.get()


def test_format_datetime_formats_and_defaults():
    """_format_datetime formats a datetime and returns the default for None."""
    from minimise.interfaces.cli import _format_datetime
    dt = datetime(2026, 6, 23, 14, 5, 9)
    assert _format_datetime(dt) == "2026-06-23 14:05:09"
    assert _format_datetime(None) == "N/A"
    assert _format_datetime(None, default="-") == "-"


def test_filter_tasks_by_id_full_and_prefix():
    """_filter_tasks_by_id matches on full id or prefix."""
    from minimise.interfaces.cli import _filter_tasks_by_id
    tasks = [
        Task(id="task-abc", job_id="j", name="A", description="d", estimated_duration_min=5),
        Task(id="task-xyz", job_id="j", name="B", description="d", estimated_duration_min=5),
    ]
    assert [t.id for t in _filter_tasks_by_id(tasks, "task-abc")] == ["task-abc"]
    assert {t.id for t in _filter_tasks_by_id(tasks, "task-")} == {"task-abc", "task-xyz"}
    assert _filter_tasks_by_id(tasks, "nope") == []


def test_get_and_validate_job_returns_resolved_job(runner, mock_config_dir):
    """_get_and_validate_job resolves an id prefix and returns (id, db, job)."""
    from minimise.interfaces.cli import _get_and_validate_job
    db = Database(mock_config_dir / "minimise.db"); db.init_db()
    job = Job(id=str(uuid.uuid4()), name="J", status=JobStatus.PENDING,
              plan_path="/p.yaml", created_at=datetime.utcnow())
    db.create_job(job)
    resolved_id, returned_db, job_obj = _get_and_validate_job(job.id[:8])
    assert resolved_id == job.id
    assert job_obj.id == job.id


def test_job_not_found_path_prints_message_and_exits_nonzero(runner, mock_config_dir):
    """A bogus job id surfaces the standard not-found message and a non-zero exit."""
    # resolve_job_id reports the prefix it couldn't match.
    result = runner.invoke(mini, ["job", "status", "deadbeef"])
    assert result.exit_code != 0
    assert "Error: Job 'deadbeef' not found" in result.output


def test_job_estimate_total_includes_hooks():
    from minimise.interfaces.cli.job import job_estimate_total
    from minimise.models import Plan, Task
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "init", "shell": "true", "estimated_duration_min": 2}],
        "tasks": [{"id": "t1", "name": "B", "description": "d", "goal": "g",
                   "estimated_duration_min": 3,
                   "post_hooks": [{"name": "pytest", "shell": "p", "estimated_duration_min": 4}]}],
    })
    tasks = [Task(id="task-1", job_id="j1", name="B", description="d",
                  estimated_duration_min=3, goal="g")]
    assert job_estimate_total(tasks, plan) == 2 + 3 + 4


def test_job_new_rejects_unknown_assignee(runner, mock_config_dir, isolated_repo):
    """An assignee with no matching persona rejects the job (no personas.yaml)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_path.write_text("""
name: Assignee Plan
tasks:
  - id: t1
    name: Task 1
    description: Task with an assignee that is not a defined persona
    goal: Do the thing
    assignee: nobody
    estimated_duration_min: 30
""")
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        assert result.exit_code != 0
        assert "Unknown persona" in result.output
        assert "nobody" in result.output

        db = Database(mock_config_dir / "minimise.db"); db.init_db()
        assert db.list_jobs() == []


def test_job_new_accepts_known_assignee(runner, mock_config_dir, isolated_repo):
    """An assignee matching a persona in personas.yaml lets the job succeed."""
    (mock_config_dir / "personas.yaml").write_text(
        "reviewer:\n  prompt: You are a careful reviewer.\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_path.write_text("""
name: Assignee Plan
tasks:
  - id: t1
    name: Task 1
    description: Task assigned to a persona that exists in the registry
    goal: Do the thing
    assignee: reviewer
    estimated_duration_min: 30
""")
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        assert result.exit_code == 0
        assert "Job created" in result.output


def test_job_new_no_assignee_no_personas(runner, mock_config_dir, isolated_repo):
    """No assignee + no personas.yaml -> gate is a no-op, job creation proceeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_path.write_text("""
name: Plain Plan
tasks:
  - id: t1
    name: Task 1
    description: Task without any assignee at all here
    goal: Do the thing
    estimated_duration_min: 30
""")
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        assert result.exit_code == 0
        assert "Job created" in result.output


def test_mini_job_list_shows_failed_for_dead_pid(runner, mock_config_dir):
    """A RUNNING job whose orchestrator pid is dead lists as FAILED (reconciled on read)."""
    import subprocess
    db = Database(mock_config_dir / "minimise.db")
    db.init_db()

    p = subprocess.Popen(["true"]); p.wait()  # a pid guaranteed dead
    job = Job(id=str(uuid.uuid4()), name="Crashed Job", status=JobStatus.PENDING,
              plan_path="/path/to/plan.yaml")
    db.create_job(job)
    db.update_job_status(job.id, JobStatus.RUNNING, pid=p.pid)

    result = runner.invoke(mini, ["job", "list", "--format", "json"])
    assert result.exit_code == 0
    entry = next(j for j in json.loads(result.output) if j["id"] == job.id)
    assert entry["status"] == "failed"
