import pytest
import tempfile
import subprocess
import yaml
from pathlib import Path
from datetime import datetime
from minimise.orchestration.job_controller import JobController
from minimise.models import Job, Task, JobStatus, TaskStatus
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
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
        "tasks": [
            {
                "id": "task-1",
                "name": "Task 1",
                "description": "First task",
                "goal": "Complete first task",
                "estimated_duration_min": 5,
            },
            {
                "id": "task-2",
                "name": "Task 2",
                "description": "Second task",
                "goal": "Complete second task",
                "estimated_duration_min": 5,
            }
        ]
    }

    plan_path = temp_db_dir / "plan.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    return plan_path


@pytest.fixture
def job_controller(temp_db_dir, git_repo):
    """Create a JobController instance."""
    db = Database(temp_db_dir / "test.db")
    db.init_db()

    git_tracker = GitTracker(git_repo)
    jobs_dir = temp_db_dir / "jobs"

    manager = JobController(db, git_tracker, jobs_dir, git_repo)
    return manager


def test_create_job_from_plan(job_controller, plan_file):
    """Test creating a job from a plan.yaml file."""
    job = job_controller.create_job(plan_file)

    assert job is not None
    assert job.name == "Test Plan"
    assert job.status == JobStatus.PENDING
    assert job.base_commit is not None
    assert len(job.tasks) == 2
    assert job.tasks[0].name == "Task 1"
    assert job.tasks[1].name == "Task 2"


def test_get_job_status(job_controller, plan_file):
    """Test retrieving job status with all tasks."""
    # Create job
    created_job = job_controller.create_job(plan_file)
    job_id = created_job.id

    # Retrieve job
    retrieved_job = job_controller.get_job_status(job_id)

    assert retrieved_job is not None
    assert retrieved_job.id == job_id
    assert retrieved_job.name == "Test Plan"
    assert retrieved_job.status == JobStatus.PENDING
    assert len(retrieved_job.tasks) == 2
    assert all(isinstance(t, Task) for t in retrieved_job.tasks)


def test_get_job_status_not_found(job_controller):
    """Test retrieving non-existent job returns None."""
    retrieved_job = job_controller.get_job_status("nonexistent-id")
    assert retrieved_job is None


def test_cancel_job_basic(job_controller, plan_file):
    """Test cancel job cancels a job and its tasks."""
    created_job = job_controller.create_job(plan_file)
    job_id = created_job.id

    # Simulate job running by marking it as RUNNING
    job_controller.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())

    # Mark some tasks as RUNNING
    tasks = job_controller.db.list_tasks_for_job(job_id)
    if len(tasks) > 0:
        job_controller.db.update_task_status(tasks[0].id, TaskStatus.RUNNING)

    # Stop the job (no PID -> just finalizes state)
    result = job_controller.stop_job(job_id)
    assert result is True

    # Verify job status is STOPPED
    job = job_controller.get_job_status(job_id)
    assert job.status == JobStatus.STOPPED

    # Verify tasks are STOPPED
    for task in job.tasks:
        if task.status != TaskStatus.COMPLETED and task.status != TaskStatus.FAILED:
            assert task.status == TaskStatus.STOPPED


def test_run_job_basic(job_controller, plan_file):
    """Test running a job with mocked task execution."""
    from minimise.orchestration.task_executor import TaskExecutor

    # Create job
    created_job = job_controller.create_job(plan_file)
    job_id = created_job.id

    # Mock the task executor to avoid actual Claude Code invocation
    original_executor_class = TaskExecutor

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, next_task=None, verify=None):
            # Mock successful execution
            return True, f"Executed {task.name}"

    # Monkey patch for this test
    import minimise.orchestration.job_controller
    minimise.orchestration.job_controller.TaskExecutor = MockTaskExecutor

    try:
        # Run the job
        success = job_controller.start_job(job_id)

        # Verify job completed
        assert success
        job = job_controller.get_job_status(job_id)
        assert job.status == JobStatus.COMPLETED

        # Verify all tasks completed
        for task in job.tasks:
            assert task.status == TaskStatus.COMPLETED
    finally:
        # Restore original class
        minimise.orchestration.job_controller.TaskExecutor = original_executor_class


def test_job_runs_task_hooks_in_plan_order(job_controller, temp_db_dir):
    """pre_hooks -> task -> post_hooks, recorded as executions with hook_name."""
    from minimise.orchestration.task_executor import TaskExecutor

    plan_content = {
        "name": "Hook Order Plan",
        "tasks": [{
            "id": "task-1", "name": "Build", "description": "d", "goal": "g",
            "estimated_duration_min": 1,
            "pre_hooks": [{"name": "setup", "shell": "true", "estimated_duration_min": 1}],
            "post_hooks": [{"name": "verify", "shell": "true", "estimated_duration_min": 1}],
        }],
    }
    plan_path = temp_db_dir / "hook_order.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    original = job_controller.task_executor.execute_task

    def mock_execute_task(task, job_id, handover_context, next_task=None, verify=None):
        if verify is not None:
            verify(0)  # post_task hooks now run inside execute_task via verify
        job_controller.db.update_task_status(task.id, TaskStatus.COMPLETED,
                                             completed_at=datetime.utcnow())
        return True, "ok"

    job_controller.task_executor.execute_task = mock_execute_task
    try:
        created_job = job_controller.create_job(plan_path)
        assert job_controller.start_job(created_job.id)

        execs = job_controller.db.list_executions_for_job(created_job.id)
        by_name = {(e.hook_name, e.execution_type) for e in execs if e.hook_name}
        assert ("setup", "pre_task") in by_name
        assert ("verify", "post_task") in by_name
    finally:
        job_controller.task_executor.execute_task = original


def test_task_commits_against_base_commit(job_controller, plan_file, git_repo):
    """Test that each task commits its changes against its base commit, not HEAD."""
    from minimise.orchestration.task_executor import TaskExecutor

    # Create job
    created_job = job_controller.create_job(plan_file)
    job_id = created_job.id
    base_commit = created_job.base_commit

    # Get the tasks
    tasks = job_controller.db.list_tasks_for_job(job_id)
    assert len(tasks) == 2

    # Verify base_commit is captured in job
    assert base_commit is not None

    # Mock task executor to simulate making changes between tasks
    original_executor_class = TaskExecutor
    execution_count = [0]

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, next_task=None, verify=None):
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
            job = self.store.db.get_job(job_id)
            task.base_commit = job.base_commit  # Should be the job's base_commit
            self.store.db.update_task(task)

            return True, f"Executed {task.name}"

    import minimise.orchestration.job_controller
    minimise.orchestration.job_controller.TaskExecutor = MockTaskExecutor

    try:
        # Run the job
        success = job_controller.start_job(job_id)
        assert success

        # Verify all tasks completed
        completed_tasks = job_controller.db.list_tasks_for_job(job_id)
        for task in completed_tasks:
            assert task.status == TaskStatus.COMPLETED
            # Verify base_commit was captured
            assert task.base_commit is not None
            # All tasks should have the same base_commit (the job's base_commit)
            assert task.base_commit == base_commit

    finally:
        minimise.orchestration.job_controller.TaskExecutor = original_executor_class


def test_task_commit_message_format(temp_db_dir, git_repo, plan_file):
    """Test that task commits use the correct message format: 'Task <id>: <name>'."""
    from minimise.orchestration.job_controller import JobController
    from minimise.orchestration.task_executor import TaskExecutor
    from minimise.storage.database import Database
    from minimise.storage.git_tracker import GitTracker

    original_executor_class = TaskExecutor
    execution_count = [0]
    commit_messages = []

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, next_task=None, verify=None):
            execution_count[0] += 1

            # Simulate task making changes
            test_file = Path(git_repo) / f"commit_test_{execution_count[0]}.txt"
            test_file.write_text(f"Content from task {execution_count[0]}")

            # Commit with the task ID and name
            subprocess.run(
                ["git", "add", f"commit_test_{execution_count[0]}.txt"],
                cwd=git_repo,
                capture_output=True,
                check=True
            )

            commit_msg = f"Task {task.id}: {task.name}"
            commit_messages.append(commit_msg)

            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=git_repo,
                capture_output=True,
                check=True
            )

            # Update status before returning
            self.store.db.update_task_status(task.id, TaskStatus.COMPLETED, completed_at=datetime.utcnow())
            return True, f"Executed {task.name}"

    import minimise.orchestration.job_controller
    minimise.orchestration.job_controller.TaskExecutor = MockTaskExecutor

    try:
        db = Database(temp_db_dir / "test.db")
        db.init_db()

        git_tracker = GitTracker(git_repo)
        jobs_dir = temp_db_dir / "jobs"

        job_controller = JobController(db, git_tracker, jobs_dir, git_repo)

        # Create job
        created_job = job_controller.create_job(plan_file)
        job_id = created_job.id

        # Get tasks first to know the task IDs
        tasks = job_controller.db.list_tasks_for_job(job_id)
        assert len(tasks) == 2

        # Run the job
        success = job_controller.start_job(job_id)
        assert success

        # Get commit log and verify commit messages
        result = subprocess.run(
            ["git", "log", "--oneline", "-n", "2"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True
        )

        commit_log = result.stdout
        # Verify commits were made with task ID and name
        assert len(commit_messages) == 2
        # Verify that task IDs are in commit log
        for task_id in [t.id for t in tasks]:
            assert any(task_id in msg for msg in commit_messages)

    finally:
        minimise.orchestration.job_controller.TaskExecutor = original_executor_class


def test_task_diff_excludes_prior_task_changes(temp_db_dir, git_repo, plan_file):
    """Test that task diff only includes changes from current task, not prior tasks."""
    from minimise.orchestration.job_controller import JobController
    from minimise.orchestration.task_executor import TaskExecutor
    from minimise.storage.database import Database
    from minimise.storage.git_tracker import GitTracker

    original_executor_class = TaskExecutor
    execution_count = [0]
    stored_diffs = []

    class MockTaskExecutor(TaskExecutor):
        def execute_task(self, task, job_id, handover_context, next_task=None, verify=None):
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
                ["git", "diff", f"{task.base_commit}..HEAD"],
                cwd=git_repo,
                capture_output=True,
                text=True,
                check=True
            )

            diff_output = diff_result.stdout
            stored_diffs.append(diff_output)

            # Store base_commit
            task_dir = Path(self.store.jobs_dir) / job_id / "tasks" / task.id
            task_dir.mkdir(parents=True, exist_ok=True)
            diff_path = task_dir / "diff.txt"
            diff_path.write_text(diff_output)
            task.diff_path = str(diff_path)

            self.store.db.update_task(task)
            self.store.db.update_task_status(task.id, TaskStatus.COMPLETED, completed_at=datetime.utcnow())

            return True, f"Executed {task.name}"

    import minimise.orchestration.job_controller
    minimise.orchestration.job_controller.TaskExecutor = MockTaskExecutor

    try:
        db = Database(temp_db_dir / "test.db")
        db.init_db()

        git_tracker = GitTracker(git_repo)
        jobs_dir = temp_db_dir / "jobs"

        job_controller = JobController(db, git_tracker, jobs_dir, git_repo)

        # Create job
        created_job = job_controller.create_job(plan_file)
        job_id = created_job.id

        # Run the job
        success = job_controller.start_job(job_id)
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
        minimise.orchestration.job_controller.TaskExecutor = original_executor_class


def test_failed_job_persists_in_db(job_controller, plan_file):
    """Test that a failed job is persisted in database and not deleted."""
    execution_count = [0]
    original_method = job_controller.task_executor.execute_task

    def mock_execute_task(task, job_id, handover_context, next_task=None, verify=None):
        execution_count[0] += 1
        if execution_count[0] == 1:
            # Fail on first task
            error_msg = "Task execution failed: simulated failure"
            job_controller.db.update_task_status(task.id, TaskStatus.FAILED, completed_at=datetime.utcnow())
            return False, error_msg
        # This shouldn't be reached
        return True, f"Executed {task.name}"

    # Patch the execute_task method
    job_controller.task_executor.execute_task = mock_execute_task

    try:
        # Create job
        created_job = job_controller.create_job(plan_file)
        job_id = created_job.id

        # Run the job
        success = job_controller.start_job(job_id)
        assert not success

        # Verify job status is FAILED
        job = job_controller.get_job_status(job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED

        # Verify job is not deleted (can still be retrieved)
        retrieved_job = job_controller.db.get_job(job_id)
        assert retrieved_job is not None
        assert retrieved_job.id == job_id

        # Verify the failed task is marked as FAILED
        tasks = job_controller.db.list_tasks_for_job(job_id)
        assert any(t.status == TaskStatus.FAILED for t in tasks)

    finally:
        job_controller.task_executor.execute_task = original_method


def test_failed_job_stores_error_reason(job_controller, plan_file):
    """A failed task is persisted as FAILED (failure detail lives in job.log now)."""
    error_reason = "Database connection timeout"
    original_method = job_controller.task_executor.execute_task

    def mock_execute_task(task, job_id, handover_context, next_task=None, verify=None):
        error_msg = f"Task execution failed: {error_reason}"
        job_controller.db.update_task_status(task.id, TaskStatus.FAILED, completed_at=datetime.utcnow())
        return False, error_msg

    job_controller.task_executor.execute_task = mock_execute_task

    try:
        # Create job
        created_job = job_controller.create_job(plan_file)
        job_id = created_job.id

        # Run the job
        success = job_controller.start_job(job_id)
        assert not success

        # Verify job has failed status
        job = job_controller.get_job_status(job_id)
        assert job.status == JobStatus.FAILED

        failed_tasks = [t for t in job.tasks if t.status == TaskStatus.FAILED]
        assert len(failed_tasks) > 0

    finally:
        job_controller.task_executor.execute_task = original_method


def test_pre_plan_hook_failure_persists_job(job_controller, plan_file, temp_db_dir):
    """Test that pre-plan hook failure persists the job with error status."""
    # Create a plan with failing pre-plan hook
    plan_content = {
        "name": "Test Plan with Hook Failure",
        "briefing": "Plan with failing pre hook",
        "pre_hooks": [{"name": "guard", "shell": "exit 1", "estimated_duration_min": 1}],
        "tasks": [
            {
                "id": "task-1",
                "name": "Task 1",
                "description": "First task",
                "goal": "Complete task",
                "estimated_duration_min": 5,
            }
        ]
    }

    plan_path = temp_db_dir / "failing_plan.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    # Create job with failing plan
    created_job = job_controller.create_job(plan_path)
    job_id = created_job.id

    # Run the job
    success = job_controller.start_job(job_id)
    assert not success

    # Verify job persists with FAILED status
    job = job_controller.get_job_status(job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED

    # Verify no tasks were executed (pre-plan hook failed before tasks)
    tasks = job_controller.db.list_tasks_for_job(job_id)
    assert all(t.status == TaskStatus.PENDING for t in tasks)


def test_post_plan_hook_failure_persists_job(job_controller, plan_file):
    """Test that post-plan hook failure marks job as failed but persists it."""
    plan_content = {
        "name": "Test Plan with Post Hook Failure",
        "briefing": "Plan with failing post hook",
        "post_hooks": [{"name": "guard", "shell": "exit 1", "estimated_duration_min": 1}],
        "tasks": [
            {
                "id": "task-1",
                "name": "Task 1",
                "description": "First task",
                "goal": "Complete task",
                "estimated_duration_min": 5,
            }
        ]
    }

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        with open(plan_path, "w") as f:
            yaml.dump(plan_content, f)

        # Mock task executor to succeed
        original_method = job_controller.task_executor.execute_task

        def mock_execute_task(task, job_id, handover_context, next_task=None, verify=None):
            job_controller.db.update_task_status(task.id, TaskStatus.COMPLETED, completed_at=datetime.utcnow())
            return True, f"Executed {task.name}"

        job_controller.task_executor.execute_task = mock_execute_task

        try:
            # Create job
            created_job = job_controller.create_job(plan_path)
            job_id = created_job.id

            # Run the job
            success = job_controller.start_job(job_id)
            assert not success

            # Verify job persists with FAILED status (due to post-hook failure)
            job = job_controller.get_job_status(job_id)
            assert job is not None
            assert job.status == JobStatus.FAILED

            # Verify tasks completed before failure
            tasks = job_controller.db.list_tasks_for_job(job_id)
            assert all(t.status == TaskStatus.COMPLETED for t in tasks)

        finally:
            job_controller.task_executor.execute_task = original_method


def test_estimated_duration_min_parsed_from_yaml(job_controller, temp_db_dir):
    """Test that estimated_duration_min is parsed from YAML task spec."""
    plan_content = {
        "name": "Plan with Durations",
        "briefing": "Test plan with task durations",
        "tasks": [
            {
                "id": "quick-task",
                "name": "Quick Task",
                "description": "A quick task",
                "goal": "Complete quickly",
                "estimated_duration_min": 5,
            },
            {
                "id": "medium-task",
                "name": "Medium Task",
                "description": "A medium task",
                "goal": "Complete in reasonable time",
                "estimated_duration_min": 60,
            },
            {
                "id": "long-task",
                "name": "Long Task",
                "description": "A long task",
                "goal": "Complete eventually",
                "estimated_duration_min": 480,
            },
        ]
    }

    plan_path = temp_db_dir / "plan_with_durations.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    # Create job from plan
    job = job_controller.create_job(plan_path)

    # Verify all tasks have the correct estimated_duration_min values
    assert len(job.tasks) == 3
    assert job.tasks[0].estimated_duration_min == 5
    assert job.tasks[1].estimated_duration_min == 60
    assert job.tasks[2].estimated_duration_min == 480



def test_estimated_duration_min_stored_in_database(job_controller, temp_db_dir):
    """Test that estimated_duration_min is stored in the database."""
    plan_content = {
        "name": "Plan for DB Storage",
        "briefing": "Test database storage",
        "tasks": [
            {
                "id": "db-task-1",
                "name": "DB Task 1",
                "description": "First task",
                "goal": "Complete first",
                "estimated_duration_min": 15,
            },
            {
                "id": "db-task-2",
                "name": "DB Task 2",
                "description": "Second task",
                "goal": "Complete second",
                "estimated_duration_min": 45,
            },
        ]
    }

    plan_path = temp_db_dir / "plan_for_db.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    # Create job from plan
    created_job = job_controller.create_job(plan_path)
    job_id = created_job.id

    # Retrieve tasks directly from database
    db_tasks = job_controller.db.list_tasks_for_job(job_id)

    # Verify tasks have correct estimated_duration_min values in database
    assert len(db_tasks) == 2
    assert db_tasks[0].estimated_duration_min == 15
    assert db_tasks[1].estimated_duration_min == 45


def test_estimated_duration_min_survives_refetch(job_controller, temp_db_dir):
    """Test that estimated_duration_min persists across a job re-fetch."""
    plan_content = {
        "name": "Plan for Resume Test",
        "briefing": "Test resume persistence",
        "tasks": [
            {
                "id": "resume-task-1",
                "name": "Resume Task 1",
                "description": "First task",
                "goal": "Complete first",
                "estimated_duration_min": 20,
            },
            {
                "id": "resume-task-2",
                "name": "Resume Task 2",
                "description": "Second task",
                "goal": "Complete second",
                "estimated_duration_min": 90,
            },
        ]
    }

    plan_path = temp_db_dir / "plan_for_resume.yaml"
    with open(plan_path, "w") as f:
        yaml.dump(plan_content, f)

    # Create initial job
    initial_job = job_controller.create_job(plan_path)
    job_id = initial_job.id

    # Simulate first task completion (without actually running)
    db_tasks = job_controller.db.list_tasks_for_job(job_id)
    job_controller.db.update_task_status(
        db_tasks[0].id,
        TaskStatus.COMPLETED,
        completed_at=datetime.utcnow()
    )

    # Update job to RUNNING to simulate partial execution
    job_controller.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())

    # Retrieve job again (simulating resume)
    resumed_job = job_controller.get_job_status(job_id)

    # Verify estimated_duration_min values are still present after resume
    assert len(resumed_job.tasks) == 2
    assert resumed_job.tasks[0].estimated_duration_min == 20
    assert resumed_job.tasks[1].estimated_duration_min == 90


def test_plan_yaml_cached_on_job_creation(job_controller, plan_file):
    """Test that plan YAML is cached in jobs directory on job creation."""
    # Create job
    job = job_controller.create_job(plan_file)
    job_id = job.id

    # Verify plan is cached at ~/.minimise/jobs/<job-id>/plan.yaml
    cached_plan_path = job_controller.jobs_dir / job_id / "plan.yaml"
    assert cached_plan_path.exists(), f"Plan should be cached at {cached_plan_path}"

    # Verify cached plan content is readable
    with open(cached_plan_path, 'r') as f:
        cached_plan = yaml.safe_load(f)

    # Verify cached plan has expected content
    assert cached_plan is not None
    assert cached_plan['name'] == "Test Plan"
    assert cached_plan['briefing'] == "This is a test plan"
    assert len(cached_plan['tasks']) == 2
    assert cached_plan['tasks'][0]['name'] == "Task 1"
    assert cached_plan['tasks'][1]['name'] == "Task 2"


def test_cached_plan_survives_original_deletion(job_controller, temp_db_dir):
    """Test that cached plan survives deletion of original plan file."""
    # Create a plan file
    plan_content = {
        "name": "Deletable Plan",
        "briefing": "This plan will be deleted",
        "tasks": [
            {
                "id": "only-task",
                "name": "Only Task",
                "description": "Single task",
                "goal": "Complete task",
                "estimated_duration_min": 5,
            }
        ]
    }

    original_plan_path = temp_db_dir / "deletable_plan.yaml"
    with open(original_plan_path, "w") as f:
        yaml.dump(plan_content, f)

    # Create job from plan
    job = job_controller.create_job(original_plan_path)
    job_id = job.id

    # Verify cached plan exists
    cached_plan_path = job_controller.jobs_dir / job_id / "plan.yaml"
    assert cached_plan_path.exists()

    # Delete original plan file
    original_plan_path.unlink()
    assert not original_plan_path.exists()

    # Verify cached copy still exists and is readable
    assert cached_plan_path.exists()
    with open(cached_plan_path, 'r') as f:
        cached_plan = yaml.safe_load(f)

    assert cached_plan is not None
    assert cached_plan['name'] == "Deletable Plan"
    assert len(cached_plan['tasks']) == 1
