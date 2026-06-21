import pytest
import tempfile
import subprocess
import yaml
from pathlib import Path
from datetime import datetime
from minimise.job_manager import JobManager
from minimise.models import Job, Task, JobStatus, TaskStatus
from minimise.database import Database
from minimise.git_tracker import GitTracker
import uuid


@pytest.fixture
def git_repo():
    """Create a temporary git repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Initialize git repository
        subprocess.run(
            ["git", "init"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )

        # Configure git user
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )

        # Create initial commit
        test_file = repo_path / "test.txt"
        test_file.write_text("initial content")

        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )

        yield repo_path


@pytest.fixture
def plan_file(temp_db_dir):
    """Create a sample plan.yaml file."""
    plan_content = {
        "name": "Test Plan",
        "briefing": "This is a test plan",
        "pre_plan_hook": "",
        "post_plan_hook": "",
        "tasks": [
            {
                "name": "Task 1",
                "description": "First task",
                "pre_task_hook": "",
                "post_task_hook": "",
            },
            {
                "name": "Task 2",
                "description": "Second task",
                "pre_task_hook": "",
                "post_task_hook": "",
            }
        ]
    }

    plan_path = temp_db_dir / "plan.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    return plan_path


@pytest.fixture
def job_manager(temp_db_dir, git_repo):
    """Create a JobManager instance."""
    db = Database(temp_db_dir / "test.db")
    db.init_db()

    git_tracker = GitTracker(git_repo)
    jobs_dir = temp_db_dir / "jobs"

    manager = JobManager(db, git_tracker, jobs_dir, git_repo)
    return manager


def test_create_job_from_plan(job_manager, plan_file):
    """Test creating a job from a plan.yaml file."""
    job = job_manager.create_job(plan_file)

    assert job is not None
    assert job.name == "Test Plan"
    assert job.status == JobStatus.PENDING
    assert job.base_commit is not None
    assert len(job.tasks) == 2
    assert job.tasks[0].name == "Task 1"
    assert job.tasks[1].name == "Task 2"


def test_get_job_status(job_manager, plan_file):
    """Test retrieving job status with all tasks."""
    # Create job
    created_job = job_manager.create_job(plan_file)
    job_id = created_job.id

    # Retrieve job
    retrieved_job = job_manager.get_job_status(job_id)

    assert retrieved_job is not None
    assert retrieved_job.id == job_id
    assert retrieved_job.name == "Test Plan"
    assert retrieved_job.status == JobStatus.PENDING
    assert len(retrieved_job.tasks) == 2
    assert all(isinstance(t, Task) for t in retrieved_job.tasks)


def test_get_job_status_not_found(job_manager):
    """Test retrieving non-existent job returns None."""
    retrieved_job = job_manager.get_job_status("nonexistent-id")
    assert retrieved_job is None


def test_cancel_job_basic(job_manager, plan_file):
    """Test cancel job cancels a job and its tasks."""
    created_job = job_manager.create_job(plan_file)
    job_id = created_job.id

    # Simulate job running by marking it as RUNNING
    job_manager.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())

    # Mark some tasks as RUNNING
    tasks = job_manager.db.list_tasks_for_job(job_id)
    if len(tasks) > 0:
        job_manager.db.update_task_status(tasks[0].id, TaskStatus.RUNNING)

    # Cancel the job
    result = job_manager.cancel_job(job_id)
    assert result is True

    # Verify job status is CANCELLED
    job = job_manager.get_job_status(job_id)
    assert job.status == JobStatus.CANCELLED

    # Verify tasks are CANCELLED
    for task in job.tasks:
        if task.status != TaskStatus.COMPLETED and task.status != TaskStatus.FAILED:
            assert task.status == TaskStatus.CANCELLED


def test_run_job_basic(job_manager, plan_file):
    """Test running a job with mocked task execution."""
    from minimise.task_executor import TaskExecutor

    # Create job
    created_job = job_manager.create_job(plan_file)
    job_id = created_job.id

    # Mock the task executor to avoid actual Claude Code invocation
    original_executor_class = TaskExecutor

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, pre_task_hook="", post_task_hook=""):
            # Mock successful execution
            return True, f"Executed {task.name}"

    # Monkey patch for this test
    import minimise.job_manager
    minimise.job_manager.TaskExecutor = MockTaskExecutor

    try:
        # Run the job
        success = job_manager.run_job(job_id)

        # Verify job completed
        assert success
        job = job_manager.get_job_status(job_id)
        assert job.status == JobStatus.COMPLETED

        # Verify all tasks completed
        for task in job.tasks:
            assert task.status == TaskStatus.COMPLETED
    finally:
        # Restore original class
        minimise.job_manager.TaskExecutor = original_executor_class
