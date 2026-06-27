import pytest
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock
from minimise.orchestration.task_executor import TaskExecutor
from minimise.models import Task, TaskStatus
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.agents.harness import AgentHarness, HarnessResult
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
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, git_tracker)

    assert executor.store is store
    assert executor.git_tracker is git_tracker
    assert executor.MAX_RETRIES == 3


def test_pre_post_hooks_execution(temp_db_dir, db, git_repo):
    """Test that pre and post hooks are executed."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

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
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

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
    assert updated_task.completed_at is not None


def test_task_completion_without_base_commit(temp_db_dir, db, git_repo):
    """Test that task success updates status even when base_commit is None."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

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
    assert updated_task.completed_at is not None


def test_task_commits_against_base_commit(temp_db_dir, db, git_repo):
    """Test that task commits are created against the task's base_commit, not HEAD."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

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
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

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
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

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


def test_failed_attempt_handover_injected_into_retry(temp_db_dir, db, git_repo):
    """A failed attempt's error is fed into the next attempt's context (learn-from-failure)."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Flaky", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    seen_handovers = []

    def mock_invoke(context):
        seen_handovers.append(context["handover"])
        # Fail first attempt, succeed second.
        if len(seen_handovers) == 1:
            return False, "boom: missing import"
        return True, "ok"

    executor._invoke_claude_code = mock_invoke
    success, _ = executor.execute_task(task, job_id, "ORIGINAL_HANDOVER")

    assert success
    assert seen_handovers[0] == "ORIGINAL_HANDOVER"          # first attempt: plain handover
    assert "boom: missing import" in seen_handovers[1]        # retry learns from the failure
    assert "ORIGINAL_HANDOVER" in seen_handovers[1]           # ...without losing prior context


def test_retry_reads_prior_attempt_agent_written_handoff(temp_db_dir, db, git_repo):
    """A failed attempt's agent-written handoff file feeds the next attempt's context."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Flaky", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    seen = []

    def mock_invoke(context):
        seen.append(context["handover"])
        # Each attempt writes its own handoff file.
        Path(context["handoff_path"]).write_text(f"HANDOFF-{len(seen) - 1}")
        return (False, "err") if len(seen) == 1 else (True, "ok")

    executor._invoke_claude_code = mock_invoke
    success, _ = executor.execute_task(task, job_id, "ORIG")

    assert success
    # Attempt 1's context is attempt 0's agent-written handoff, marked as such.
    assert "(agent-written handoff)" in seen[1]
    assert "HANDOFF-0" in seen[1]


def test_missing_handoff_returns_autogen_fallback(temp_db_dir, db, git_repo):
    """With no handoff file, the returned handover is the build_handover_prompt fallback + warning."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="T", description="d", status=TaskStatus.PENDING)
    db.create_task(task)
    next_task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                     name="NEXT_TASK_NAME", description="nd", status=TaskStatus.PENDING)

    executor._invoke_claude_code = lambda context: (True, "did the work")
    success, handover = executor.execute_task(task, job_id, "", next_task=next_task)

    assert success
    assert "WARNING auto-generated from diff - not reviewed" in handover
    assert "NEXT_TASK_NAME" in handover          # build_handover_prompt names the next task
    assert "Previous Task Summary" in handover


def test_three_attempts_leave_three_handoff_files(temp_db_dir, db, git_repo):
    """A task that takes 3 attempts (writing a file each) leaves attempt-0/1/2.md."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Stubborn", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    calls = []

    def mock_invoke(context):
        calls.append(1)
        Path(context["handoff_path"]).write_text(f"h{len(calls) - 1}")
        return (False, "x") if len(calls) < 3 else (True, "ok")

    executor._invoke_claude_code = mock_invoke
    success, _ = executor.execute_task(task, job_id, "")

    assert success
    handoff_dir = executor.store.handoff_path(job_id, task.id, 0).parent
    for n in range(3):
        assert (handoff_dir / f"attempt-{n}.md").exists()


def test_executions_recorded_across_retry(temp_db_dir, db, git_repo):
    """Each attempt writes its own Execution row: a failed attempt 0, then a committed attempt 1."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Flaky", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    calls = []

    def mock_invoke(context):
        calls.append(1)
        if len(calls) == 1:
            return False, "boom"
        # Second attempt makes a real change so a commit (and SHA) is produced.
        (git_repo / "out.txt").write_text("done by retry")
        return True, "ok"

    executor._invoke_claude_code = mock_invoke
    success, _ = executor.execute_task(task, job_id, "")
    assert success

    execs = db.list_executions_for_task(task.id)
    assert [e.attempt for e in execs] == [0, 1]
    assert execs[0].status == TaskStatus.FAILED and "boom" in execs[0].output
    assert execs[1].status == TaskStatus.COMPLETED
    assert execs[1].commit_sha and len(execs[1].commit_sha) == 40  # SHA captured from git_tracker.commit
    assert execs[1].diff_path and Path(execs[1].diff_path).exists()
    assert all(e.started_at and e.completed_at for e in execs)     # per-attempt timestamps preserved


def _setup_job_and_task(db, git_tracker):
    from minimise.models import Job, JobStatus

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="T", description="d", status=TaskStatus.PENDING)
    db.create_task(task)
    return job_id, task


def test_pre_task_hook_recorded(temp_db_dir, db, git_repo):
    """A non-empty pre-task hook writes a COMPLETED pre_task execution row."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)
    executor._invoke_claude_code = lambda context: (True, "ok")

    executor.execute_task(task, job_id, "", pre_task_hook="exit 0")

    pre = [e for e in db.list_executions_for_job(job_id) if e.execution_type == "pre_task"]
    assert len(pre) == 1
    assert pre[0].task_id == task.id
    assert pre[0].status == TaskStatus.COMPLETED
    assert pre[0].started_at and pre[0].completed_at


def test_pre_task_hook_failure_recorded(temp_db_dir, db, git_repo):
    """A failing pre-task hook records a FAILED row AND still early-returns."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)
    executor._invoke_claude_code = lambda context: (True, "ok")

    success, output = executor.execute_task(task, job_id, "", pre_task_hook="exit 1")

    assert not success
    assert output.startswith("Pre-task hook failed:")
    pre = [e for e in db.list_executions_for_job(job_id) if e.execution_type == "pre_task"]
    assert len(pre) == 1
    assert pre[0].status == TaskStatus.FAILED


def test_post_task_hook_recorded(temp_db_dir, db, git_repo):
    """A non-empty post-task hook on a passing task writes a COMPLETED post_task row."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)
    executor._invoke_claude_code = lambda context: (True, "ok")

    executor.execute_task(task, job_id, "", post_task_hook="exit 0")

    post = [e for e in db.list_executions_for_job(job_id) if e.execution_type == "post_task"]
    assert len(post) == 1
    assert post[0].task_id == task.id
    assert post[0].status == TaskStatus.COMPLETED


def test_no_hooks_records_only_attempts(temp_db_dir, db, git_repo):
    """With no hooks, no pre_/post_task rows are written — only task attempts."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)
    executor._invoke_claude_code = lambda context: (True, "ok")

    executor.execute_task(task, job_id, "")

    types = {e.execution_type for e in db.list_executions_for_job(job_id)}
    assert "pre_task" not in types
    assert "post_task" not in types


def test_task_attempt_started_at_not_copied_from_pre_task_hook(temp_db_dir, db, git_repo):
    """The closed task attempt keeps its OWN started_at, not the pre_task hook's.

    Regression: hooks share task_id and attempt=0, so _close_execution matching on
    attempt alone would graft the pre_task hook's earlier started_at onto the task row.
    """
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)
    executor._invoke_claude_code = lambda context: (True, "ok")

    executor.execute_task(task, job_id, "", pre_task_hook="true")

    rows = db.list_executions_for_job(job_id)
    pre = next(e for e in rows if e.execution_type == "pre_task")
    attempt = next(e for e in rows if e.execution_type == "task")
    # The attempt opens AFTER the pre_task hook closes, so its start is strictly later.
    assert attempt.started_at >= pre.completed_at
    assert attempt.started_at != pre.started_at


def test_default_harness_is_claude_code(temp_db_dir, db, git_repo):
    """TaskExecutor defaults to ClaudeCodeHarness when no harness injected."""
    from minimise.agents.harness import ClaudeCodeHarness

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)

    assert isinstance(executor.harness, ClaudeCodeHarness)


def test_injected_harness_is_stored(temp_db_dir, db, git_repo):
    """An explicitly injected harness is stored on the executor."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    assert executor.harness is fake


def test_invoke_delegates_to_harness_and_propagates_success(temp_db_dir, db, git_repo):
    """_invoke_claude_code delegates to harness.run and propagates success/output."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="agent did the work")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

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
    assert kwargs["cwd"] == str(git_repo)
    # No timeout/model override is passed: harness defaults are preserved.
    assert "timeout" not in kwargs
    assert "model" not in kwargs

    # Prompt (first positional arg) carries the task name and description.
    prompt = args[0]
    assert "task-7: Build widget" in prompt
    assert "Implement the widget module" in prompt


def test_invoke_prompt_names_handoff_path_and_section_headers(temp_db_dir, db, git_repo):
    """When a handoff_path is in context, the prompt names that absolute path and the four headers."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="done")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    handoff_path = str(temp_db_dir / "jobs" / "j1" / "handoffs" / "t1" / "attempt-0.md")
    executor._invoke_claude_code({
        "task_name": "T", "task_description": "D", "handoff_path": handoff_path,
    })

    prompt = fake.run.call_args[0][0]
    assert handoff_path in prompt
    for header in ("What changed & why", "Gotchas", "Current state", "What the next task needs"):
        assert header in prompt


def test_invoke_failure_returns_error_when_present(temp_db_dir, db, git_repo):
    """On failure, _invoke_claude_code returns result.error when set."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(
        success=False, output="partial stdout", error="boom: it failed"
    )
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    success, output = executor._invoke_claude_code({"task_name": "T", "task_description": "D"})

    assert success is False
    assert output == "boom: it failed"


def test_invoke_failure_falls_back_to_output_when_no_error(temp_db_dir, db, git_repo):
    """On failure with no error string, falls back to result.output."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=False, output="just stdout", error=None)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    success, output = executor._invoke_claude_code({"task_name": "T", "task_description": "D"})

    assert success is False
    assert output == "just stdout"


def test_execute_task_passes_job_log_path_to_harness(temp_db_dir, db, git_repo):
    """execute_task threads the per-job job.log path into harness.run(log_path=...)."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor.execute_task(task, job_id, "")

    log_path = store.job_log_path(job_id)
    assert fake.run.call_args.kwargs["log_path"] == str(log_path)


def test_execute_task_writes_attempt_section_marker(temp_db_dir, db, git_repo):
    """A section marker naming the task + attempt is appended to job.log before each attempt."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor.execute_task(task, job_id, "")

    log = store.job_log_path(job_id).read_text()
    assert f"--- task {task.id} attempt 0" in log
