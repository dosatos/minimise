import pytest
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from minimise.task_executor import TaskExecutor
from minimise.models import Task, TaskStatus
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


def test_task_executor_initialization(temp_db_dir, db, git_repo):
    """Test TaskExecutor initialization."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    assert executor.db is db
    assert executor.git_tracker is git_tracker
    assert executor.jobs_dir == temp_db_dir
    assert executor.MAX_RETRIES == 3


def test_pre_post_hooks_execution(temp_db_dir, db, git_repo):
    """Test that pre and post hooks are executed."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    job_id = str(uuid.uuid4())

    # Create the job first
    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.PENDING,
        base_commit=git_tracker.get_current_commit(),
    )
    db.create_job(job)

    task = Task(
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Test Task",
        description="Test task description",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Create marker files for testing hook execution
    pre_marker = temp_db_dir / "pre_hook.txt"
    post_marker = temp_db_dir / "post_hook.txt"

    pre_hook = f"echo 'pre-hook-ran' > {pre_marker}"
    post_hook = f"echo 'post-hook-ran' > {post_marker}"

    # Mock the Claude Code invocation to avoid actual execution
    def mock_invoke(context):
        return True, "Task completed successfully"

    executor._invoke_claude_code = mock_invoke

    success, output = executor.execute_task(
        task, job_id, "", pre_task_hook=pre_hook, post_task_hook=post_hook
    )

    # Verify hooks ran
    assert pre_marker.exists(), "Pre-hook did not run"
    assert post_marker.exists(), "Post-hook did not run"
    assert success
    assert output == "Task completed successfully"
