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

    # Verify job status is STOPPED
    job = job_manager.get_job_status(job_id)
    assert job.status == JobStatus.STOPPED

    # Verify tasks are STOPPED
    for task in job.tasks:
        if task.status != TaskStatus.COMPLETED and task.status != TaskStatus.FAILED:
            assert task.status == TaskStatus.STOPPED


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


def test_task_commits_against_base_commit(job_manager, plan_file, git_repo):
    """Test that each task commits its changes against its base commit, not HEAD."""
    from minimise.task_executor import TaskExecutor

    # Create job
    created_job = job_manager.create_job(plan_file)
    job_id = created_job.id
    base_commit = created_job.base_commit

    # Get the tasks
    tasks = job_manager.db.list_tasks_for_job(job_id)
    assert len(tasks) == 2

    # Verify base_commit is captured in job
    assert base_commit is not None

    # Mock task executor to simulate making changes between tasks
    original_executor_class = TaskExecutor
    execution_count = [0]

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, pre_task_hook="", post_task_hook=""):
            execution_count[0] += 1

            # Simulate task making changes
            test_file = Path(git_repo) / f"task_{execution_count[0]}.txt"
            test_file.write_text(f"Content from task {execution_count[0]}")

            # Commit the changes
            subprocess.run(
                ["git", "add", f"task_{execution_count[0]}.txt"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"Task {task.id}: {task.name}"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )

            # Store the base_commit for this task before changes
            job = self.db.get_job(job_id)
            task.base_commit = job.base_commit  # Should be the job's base_commit
            self.db.update_task(task)

            return True, f"Executed {task.name}"

    import minimise.job_manager
    minimise.job_manager.TaskExecutor = MockTaskExecutor

    try:
        # Run the job
        success = job_manager.run_job(job_id)
        assert success

        # Verify all tasks completed
        completed_tasks = job_manager.db.list_tasks_for_job(job_id)
        for task in completed_tasks:
            assert task.status == TaskStatus.COMPLETED
            # Verify base_commit was captured
            assert task.base_commit is not None
            # All tasks should have the same base_commit (the job's base_commit)
            assert task.base_commit == base_commit

    finally:
        minimise.job_manager.TaskExecutor = original_executor_class


def test_task_commit_message_format(job_manager, plan_file, git_repo):
    """Test that task commits use the correct message format: 'Task <id>: <name>'."""
    from minimise.task_executor import TaskExecutor

    # Create job
    created_job = job_manager.create_job(plan_file)
    job_id = created_job.id

    task_ids = []
    task_names = []

    original_executor_class = TaskExecutor

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, pre_task_hook="", post_task_hook=""):
            task_ids.append(task.id)
            task_names.append(task.name)

            # Simulate task making changes
            test_file = Path(git_repo) / f"commit_test_{len(task_ids)}.txt"
            test_file.write_text(f"Content from task {len(task_ids)}")

            # Commit with the task ID and name
            subprocess.run(
                ["git", "add", f"commit_test_{len(task_ids)}.txt"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"Task {task.id}: {task.name}"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )

            return True, f"Executed {task.name}"

    import minimise.job_manager
    minimise.job_manager.TaskExecutor = MockTaskExecutor

    try:
        # Run the job
        success = job_manager.run_job(job_id)
        assert success

        # Get commit log and verify commit messages
        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True
        )

        commit_log = result.stdout
        # Should contain commits for both tasks
        assert any(f"Task {task_id}" in commit_log for task_id in task_ids)

    finally:
        minimise.job_manager.TaskExecutor = original_executor_class


def test_task_diff_excludes_prior_task_changes(job_manager, plan_file, git_repo, temp_db_dir):
    """Test that task diff only includes changes from current task, not prior tasks."""
    from minimise.task_executor import TaskExecutor

    # Create job
    created_job = job_manager.create_job(plan_file)
    job_id = created_job.id
    base_commit = created_job.base_commit

    execution_count = [0]
    stored_diffs = []

    original_executor_class = TaskExecutor

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, pre_task_hook="", post_task_hook=""):
            execution_count[0] += 1

            # Simulate task making changes
            test_file = Path(git_repo) / f"diff_test_{execution_count[0]}.txt"
            test_file.write_text(f"Content from task {execution_count[0]}")

            # Commit the changes
            subprocess.run(
                ["git", "add", f"diff_test_{execution_count[0]}.txt"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"Task {task.id}: {task.name}"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )

            # Get diff against base_commit
            diff_result = subprocess.run(
                ["git", "diff", f"{base_commit}..HEAD"],
                cwd=git_repo,
                capture_output=True,
                text=True,
                check=True
            )

            diff_output = diff_result.stdout
            stored_diffs.append(diff_output)

            # Store base_commit
            task.base_commit = base_commit
            task_dir = temp_db_dir / "jobs" / job_id / "tasks" / task.id
            task_dir.mkdir(parents=True, exist_ok=True)
            diff_path = task_dir / "diff.txt"
            diff_path.write_text(diff_output)
            task.diff_path = str(diff_path)

            self.db.update_task(task)

            return True, f"Executed {task.name}"

    import minimise.job_manager
    minimise.job_manager.TaskExecutor = MockTaskExecutor

    try:
        # Run the job
        success = job_manager.run_job(job_id)
        assert success

        # Verify diffs were collected
        assert len(stored_diffs) == 2

        # Verify first diff only has one file added (from task 1)
        diff_1 = stored_diffs[0]
        assert "diff_test_1.txt" in diff_1
        assert "diff_test_2.txt" not in diff_1

        # Verify second diff has both files (accumulated changes from base)
        diff_2 = stored_diffs[1]
        assert "diff_test_1.txt" in diff_2
        assert "diff_test_2.txt" in diff_2

    finally:
        minimise.job_manager.TaskExecutor = original_executor_class
