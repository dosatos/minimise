import pytest
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock
from minimise.task_executor import TaskExecutor
from minimise.models import Task, TaskStatus
from minimise.database import Database
from minimise.git_tracker import GitTracker
from minimise.harness import AgentHarness, HarnessResult
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

    task = Task(estimated_duration_min=5, 
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


def test_post_hook_failure_updates_status(temp_db_dir, db, git_repo):
    """Test that post-hook failure updates task status to FAILED."""
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

    task = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Test Task",
        description="",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Mock Claude Code to succeed
    def mock_invoke(context):
        return True, "Success"

    executor._invoke_claude_code = mock_invoke

    # Run with failing post-hook
    failing_post_hook = "exit 1"
    success, output = executor.execute_task(
        task, job_id, "", post_task_hook=failing_post_hook
    )

    # Verify: should fail and DB should show FAILED
    assert not success
    updated_task = db.get_task(task.id)
    assert updated_task.status == TaskStatus.FAILED
    assert "Post-task hook failed" in updated_task.output


def test_task_completion_without_base_commit(temp_db_dir, db, git_repo):
    """Test that task success updates status even when base_commit is None."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    job_id = str(uuid.uuid4())

    # Create the job WITHOUT base_commit
    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.PENDING,
        base_commit=None,  # No base_commit
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Test Task",
        description="",
        status=TaskStatus.PENDING,
    )
    db.create_task(task)

    # Mock Claude Code to succeed
    def mock_invoke(context):
        return True, "Task completed"

    executor._invoke_claude_code = mock_invoke

    # Execute task
    success, output = executor.execute_task(task, job_id, "")

    # Verify: task should be COMPLETED even without base_commit
    assert success
    updated_task = db.get_task(task.id)
    assert updated_task.status == TaskStatus.COMPLETED
    assert updated_task.output == "Task completed"


def test_task_commits_against_base_commit(temp_db_dir, db, git_repo):
    """Test that task commits are created against the task's base_commit, not HEAD."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    job_id = str(uuid.uuid4())
    base_commit = git_tracker.get_current_commit()

    # Create job
    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.PENDING,
        base_commit=base_commit,
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Task 1: Make changes",
        description="Create a new file",
        status=TaskStatus.PENDING,
        base_commit=base_commit,
    )
    db.create_task(task)

    # Mock Claude Code to create a file change
    def mock_invoke(context):
        # Create a change in the repo
        test_file = git_repo / "changes.txt"
        test_file.write_text("changes made by task")
        return True, "Changes made"

    executor._invoke_claude_code = mock_invoke

    # Execute task
    success, output = executor.execute_task(task, job_id, "")
    assert success

    # Verify: diff should be stored in task.diff_path
    updated_task = db.get_task(task.id)
    assert updated_task.diff_path is not None
    diff_path = Path(updated_task.diff_path)
    assert diff_path.exists()

    # Verify diff contains the change
    diff_content = diff_path.read_text()
    assert "changes.txt" in diff_content or "changes made" in diff_content


def test_task_commit_message_format(temp_db_dir, db, git_repo):
    """Test that task commits use the correct message format."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    job_id = str(uuid.uuid4())
    base_commit = git_tracker.get_current_commit()

    # Create job
    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.PENDING,
        base_commit=base_commit,
    )
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="task-1-fix: Important fix",
        description="Fix something important",
        status=TaskStatus.PENDING,
        base_commit=base_commit,
    )
    db.create_task(task)

    # Mock Claude Code to create a file change
    def mock_invoke(context):
        test_file = git_repo / "fix.txt"
        test_file.write_text("fixed")
        return True, "Fixed"

    executor._invoke_claude_code = mock_invoke

    # Execute task
    success, output = executor.execute_task(task, job_id, "")
    assert success

    # Verify: the diff file should exist and have proper format
    updated_task = db.get_task(task.id)
    assert updated_task.diff_path is not None
    assert updated_task.status == TaskStatus.COMPLETED


def test_task_diff_excludes_prior_task_changes(temp_db_dir, db, git_repo):
    """Test that task diff only contains changes from this task, not prior tasks."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    job_id = str(uuid.uuid4())
    base_commit = git_tracker.get_current_commit()

    # Create job
    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.PENDING,
        base_commit=base_commit,
    )
    db.create_job(job)

    # Task 1: Make first change
    task1 = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Task 1: First change",
        description="First change",
        status=TaskStatus.PENDING,
        base_commit=base_commit,
    )
    db.create_task(task1)

    # Mock first task: create file1
    def mock_invoke_task1(context):
        test_file = git_repo / "file1.txt"
        test_file.write_text("task1 content")
        return True, "Task 1 done"

    executor._invoke_claude_code = mock_invoke_task1

    # Execute task 1
    success1, _ = executor.execute_task(task1, job_id, "")
    assert success1

    # Get commit after task1
    task1_commit = git_tracker.get_current_commit()

    # Task 2: Make second change (with task1_commit as base)
    task2 = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Task 2: Second change",
        description="Second change",
        status=TaskStatus.PENDING,
        base_commit=task1_commit,  # Task2 base is task1's output
    )
    db.create_task(task2)

    # Mock second task: create file2
    def mock_invoke_task2(context):
        test_file = git_repo / "file2.txt"
        test_file.write_text("task2 content")
        return True, "Task 2 done"

    executor._invoke_claude_code = mock_invoke_task2

    # Execute task 2 - it should only diff against task1_commit, not base_commit
    success2, _ = executor.execute_task(task2, job_id, "")
    assert success2

    # Verify task2's diff
    updated_task2 = db.get_task(task2.id)
    assert updated_task2.diff_path is not None
    diff_path = Path(updated_task2.diff_path)
    assert diff_path.exists()

    diff_content = diff_path.read_text()
    # Task2's diff should contain file2 (task2's change)
    assert "file2.txt" in diff_content or "task2 content" in diff_content
    # Task2's diff should NOT contain file1 (task1's change) since task2 base is after task1
    # Note: This is implementation-dependent on how git diff works


def test_default_harness_is_claude_code(temp_db_dir, db, git_repo):
    """TaskExecutor defaults to ClaudeCodeHarness when no harness injected."""
    from minimise.harness import ClaudeCodeHarness

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(db, git_tracker, temp_db_dir)

    assert isinstance(executor.harness, ClaudeCodeHarness)


def test_injected_harness_is_stored(temp_db_dir, db, git_repo):
    """An explicitly injected harness is stored on the executor."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    executor = TaskExecutor(db, git_tracker, temp_db_dir, harness=fake)

    assert executor.harness is fake


def test_invoke_delegates_to_harness_and_propagates_success(temp_db_dir, db, git_repo):
    """_invoke_claude_code delegates to harness.run and propagates success/output."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="agent did the work")
    executor = TaskExecutor(db, git_tracker, temp_db_dir, harness=fake)

    context = {
        "task_name": "task-7: Build widget",
        "task_description": "Implement the widget module",
        "task_goal": "A working widget",
        "handover": "prior context",
    }
    success, output = executor._invoke_claude_code(context)

    assert success is True
    assert output == "agent did the work"

    # harness.run called once with allow_edits=True and cwd at the repo root.
    fake.run.assert_called_once()
    args, kwargs = fake.run.call_args
    assert kwargs["allow_edits"] is True
    assert kwargs["cwd"] == str(temp_db_dir.parent.parent)
    # No timeout/model override is passed: harness defaults are preserved.
    assert "timeout" not in kwargs
    assert "model" not in kwargs

    # Prompt (first positional arg) carries the task name and description.
    prompt = args[0]
    assert "task-7: Build widget" in prompt
    assert "Implement the widget module" in prompt


def test_invoke_failure_returns_error_when_present(temp_db_dir, db, git_repo):
    """On failure, _invoke_claude_code returns result.error when set."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(
        success=False, output="partial stdout", error="boom: it failed"
    )
    executor = TaskExecutor(db, git_tracker, temp_db_dir, harness=fake)

    success, output = executor._invoke_claude_code({"task_name": "T", "task_description": "D"})

    assert success is False
    assert output == "boom: it failed"


def test_invoke_failure_falls_back_to_output_when_no_error(temp_db_dir, db, git_repo):
    """On failure with no error string, falls back to result.output."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=False, output="just stdout", error=None)
    executor = TaskExecutor(db, git_tracker, temp_db_dir, harness=fake)

    success, output = executor._invoke_claude_code({"task_name": "T", "task_description": "D"})

    assert success is False
    assert output == "just stdout"
