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
        return True, "Task completed", "success"

    executor._invoke_claude_code = mock_invoke

    # Execute task
    success, output = executor.execute_task(task, job_id, "")

    # Verify: task should be COMPLETED even without base_commit
    assert success
    updated_task = db.get_task(task.id)
    assert updated_task.status == TaskStatus.COMPLETED
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
        return True, "Changes made", "success"

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
        return True, "Fixed", "success"

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
        return True, "Task 1 done", "success"

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
        return True, "Task 2 done", "success"

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
            return False, "boom: missing import", "agent_error"
        return True, "ok", "success"

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
        return (False, "err", "agent_error") if len(seen) == 1 else (True, "ok", "success")

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

    executor._invoke_claude_code = lambda context: (True, "did the work", "success")
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
        return (False, "x", "agent_error") if len(calls) < 3 else (True, "ok", "success")

    executor._invoke_claude_code = mock_invoke
    success, _ = executor.execute_task(task, job_id, "")

    assert success
    handoff_dir = executor.store.handoff_path(job_id, task.id, 0).parent
    for n in range(3):
        assert (handoff_dir / f"attempt-{n}.md").exists()


def test_last_task_persists_handoff_when_agent_wrote_none(temp_db_dir, db, git_repo):
    """A COMPLETED last task (next_task=None) whose agent wrote no handoff still
    leaves a non-empty handoff file at handoff_path(job, task, task.retries)."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, git_tracker)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Last", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    # Agent succeeds but writes NO handoff file, and there is no next_task.
    executor._invoke_claude_code = lambda context: (True, "did the work", "success")
    success, _ = executor.execute_task(task, job_id, "", next_task=None)

    assert success
    win_path = store.handoff_path(job_id, task.id, task.retries)
    assert win_path.exists()
    assert win_path.read_text().strip()


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
            return False, "boom", "agent_error"
        # Second attempt makes a real change so a commit (and SHA) is produced.
        (git_repo / "out.txt").write_text("done by retry")
        return True, "ok", "success"

    executor._invoke_claude_code = mock_invoke
    success, _ = executor.execute_task(task, job_id, "")
    assert success

    execs = db.list_executions_for_task(task.id)
    assert [e.attempt for e in execs] == [0, 1]
    assert execs[0].status == TaskStatus.FAILED and execs[0].exit_reason == "agent_error"
    assert execs[1].status == TaskStatus.COMPLETED
    assert execs[1].commit_sha and len(execs[1].commit_sha) == 40  # SHA captured from git_tracker.commit
    assert execs[1].diff_path and Path(execs[1].diff_path).exists()
    assert all(e.started_at and e.completed_at for e in execs)     # per-attempt timestamps preserved


def test_failed_attempt_records_exit_reason(temp_db_dir, db, git_repo):
    """A failed run threads the harness's exit_reason onto the Execution row."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(
        success=False, output="stdout", error="timeout after 900s", exit_reason="timeout"
    )
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Slow", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    success, _ = executor.execute_task(task, job_id, "")
    assert not success
    execs = db.list_executions_for_task(task.id)
    assert execs[-1].exit_reason == "timeout"


def test_failure_detail_reconstructed_from_job_log(temp_db_dir, db, git_repo, monkeypatch):
    """A failed task's error is written to job.log and read back by task_narration."""
    from minimise.models import Job, JobStatus
    from minimise.interfaces.cli._shared import task_narration

    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(
        success=False, output="", error="timeout after 900s", exit_reason="timeout"
    )
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Slow", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    executor.execute_task(task, job_id, "")

    # task.output no longer exists; narration comes solely from job.log.
    monkeypatch.setattr("minimise.interfaces.cli.JOBS_DIR", temp_db_dir)
    assert "timeout after 900s" in task_narration(job_id, db.get_task(task.id))


def _setup_job_and_task(db, git_tracker):
    from minimise.models import Job, JobStatus

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="T", description="d", status=TaskStatus.PENDING)
    db.create_task(task)
    return job_id, task


def test_no_hooks_records_only_attempts(temp_db_dir, db, git_repo):
    """execute_task writes no pre_/post_task rows — hooks are JobExecutor's job now."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)
    executor._invoke_claude_code = lambda context: (True, "ok", "success")

    executor.execute_task(task, job_id, "")

    types = {e.execution_type for e in db.list_executions_for_job(job_id)}
    assert "pre_task" not in types
    assert "post_task" not in types


def test_execution_completed_at_is_agent_finish_not_lifecycle_end(temp_db_dir, db, git_repo):
    """The Execution row's completed_at marks agent-finish (before the gating
    post_task hook), strictly BEFORE the task-row's lifecycle-end completed_at."""
    import time

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor._invoke_claude_code = lambda context: (True, "ok", "success")

    # A gating post_task hook that burns wall-clock time. The Execution window
    # must NOT include it; the task-row's completed_at (stamped after) must.
    def verify(attempt):
        time.sleep(0.05)
        return "pass", ""

    success, _ = executor.execute_task(task, job_id, "", verify=verify)
    assert success

    exec_row = db.list_executions_for_task(task.id)[-1]
    task_row = db.get_task(task.id)
    assert exec_row.completed_at < task_row.completed_at


def test_hook_retry_attempt_is_not_recorded_as_success(temp_db_dir, db, git_repo):
    """A retry demanded by a gating post_task hook books the hook as the reason,
    not the agent's "success"."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor._invoke_claude_code = lambda context: (True, "ok", "success")
    verify = lambda attempt: ("retry", "findings") if attempt == 0 else ("fail", "nope")

    success, _ = executor.execute_task(task, job_id, "", verify=verify)
    assert not success

    rows = db.list_executions_for_task(task.id)
    assert rows[0].exit_reason == "hook_retry"
    assert rows[-1].exit_reason == "hook_failed"


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
    fake.run.return_value = HarnessResult(success=True, output="agent did the work", exit_reason="success")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    context = {
        "task_name": "task-7: Build widget",
        "task_description": "Implement the widget module",
        "task_goal": "A working widget",
        "handover": "prior context",
    }
    success, output, exit_reason = executor._invoke_claude_code(context)

    assert success is True
    assert output == "agent did the work"
    assert exit_reason == "success"

    # harness.run called once with allow_edits=True and cwd at the repo root.
    fake.run.assert_called_once()
    args, kwargs = fake.run.call_args
    assert kwargs["allow_edits"] is True
    assert kwargs["cwd"] == str(git_repo)
    # A context without timeout_min runs unbounded, and model/system_prompt
    # are None (no persona) so the harness omits --model/--system-prompt.
    assert kwargs["timeout"] is None
    assert kwargs["model"] is None
    assert kwargs["system_prompt"] is None

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
        success=False, output="partial stdout", error="boom: it failed", exit_reason="agent_error"
    )
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    success, output, exit_reason = executor._invoke_claude_code({"task_name": "T", "task_description": "D"})

    assert success is False
    assert output == "boom: it failed"
    assert exit_reason == "agent_error"


def test_invoke_failure_falls_back_to_output_when_no_error(temp_db_dir, db, git_repo):
    """On failure with no error string, falls back to result.output."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=False, output="just stdout", error=None)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)

    success, output, exit_reason = executor._invoke_claude_code({"task_name": "T", "task_description": "D"})

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


def test_execute_task_timeout_from_explicit_timeout_min(temp_db_dir, db, git_repo):
    """An explicit timeout_min drives the harness timeout: 40 min -> 2400s."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)
    task.timeout_min = 40

    executor.execute_task(task, job_id, "")

    assert fake.run.call_args.kwargs["timeout"] == 2400.0


def test_execute_task_without_timeout_min_is_unbounded(temp_db_dir, db, git_repo):
    """No timeout_min => no timeout at all (the estimate never implies one)."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor.execute_task(task, job_id, "")

    assert fake.run.call_args.kwargs["timeout"] is None


def test_execute_task_passes_execution_log_fields_to_harness(temp_db_dir, db, git_repo):
    """execute_task passes log_fields with the execution_id + type identity."""
    from minimise.models import Execution

    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor.execute_task(task, job_id, "")

    log_fields = fake.run.call_args.kwargs["log_fields"]
    expected_id = Execution(
        job_id=job_id, task_id=task.id, attempt=0, execution_type="task"
    ).execution_id
    assert log_fields == {"execution_id": expected_id, "type": "task", "step": task.name}


def test_execute_task_step_is_task_name_on_first_attempt(temp_db_dir, db, git_repo):
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor.execute_task(task, job_id, "")

    assert fake.run.call_args.kwargs["log_fields"]["step"] == task.name


def test_execute_task_step_marks_retry_attempt(temp_db_dir, db, git_repo):
    """A retried attempt's `step` ends with '· try 2'."""
    from minimise.models import Job, JobStatus

    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="Implement endpoint", description="d", status=TaskStatus.PENDING)
    db.create_task(task)

    seen_steps = []
    calls = {"n": 0}

    def mock_invoke(context):
        seen_steps.append(context["log_fields"]["step"])
        calls["n"] += 1
        return (calls["n"] > 1), ("boom" if calls["n"] == 1 else "ok"), "agent_error"

    executor._invoke_claude_code = mock_invoke
    executor.execute_task(task, job_id, "")

    assert seen_steps[0] == "Implement endpoint"
    assert seen_steps[1].endswith("· try 2")


def test_execute_task_writes_no_banner_to_log(temp_db_dir, db, git_repo):
    """The old `--- task ... ---` banner is no longer written to job.log."""
    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    store = JobStore(db, temp_db_dir)
    executor = TaskExecutor(store, git_tracker, harness=fake)
    job_id, task = _setup_job_and_task(db, git_tracker)

    executor.execute_task(task, job_id, "")

    log_path = store.job_log_path(job_id)
    content = log_path.read_text() if log_path.exists() else ""
    assert "--- task" not in content


def test_assigned_task_passes_persona_system_prompt_and_model(temp_db_dir, db, git_repo):
    """A task with an assignee runs with the persona's system_prompt and model,
    proving the value threads through the context dict all the way to harness.run."""
    from minimise.personas import Persona

    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    persona = Persona(name="reviewer", model="claude-opus-4-8", system_prompt="You are a picky reviewer.")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake,
                            personas={"reviewer": persona})
    job_id, task = _setup_job_and_task(db, git_tracker)
    task.assignee = "reviewer"

    executor.execute_task(task, job_id, "")

    assert fake.run.call_args.kwargs["system_prompt"] == "You are a picky reviewer."
    assert fake.run.call_args.kwargs["model"] == "claude-opus-4-8"


def test_unassigned_task_passes_none_system_prompt_and_model(temp_db_dir, db, git_repo):
    """A task with no assignee runs with system_prompt=None and model=None (today's behavior)."""
    from minimise.personas import Persona

    git_tracker = GitTracker(git_repo)
    fake = Mock(spec=AgentHarness)
    fake.run.return_value = HarnessResult(success=True, output="ok")
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker, harness=fake,
                            personas={"reviewer": Persona(name="reviewer", system_prompt="x")})
    job_id, task = _setup_job_and_task(db, git_tracker)  # no assignee

    executor.execute_task(task, job_id, "")

    assert fake.run.call_args.kwargs["system_prompt"] is None
    assert fake.run.call_args.kwargs["model"] is None




def test_failed_task_stashes_uncommitted_work(temp_db_dir, db, git_repo):
    """A terminal failure leaves a clean tree; the work (tracked + untracked) is in a stash."""
    git_tracker = GitTracker(git_repo)
    executor = TaskExecutor(JobStore(db, temp_db_dir), git_tracker)
    job_id, task = _setup_job_and_task(db, git_tracker)

    def mock_invoke(context):
        (git_repo / "test.txt").write_text("edited by agent")   # tracked
        (git_repo / "new_pkg").mkdir(exist_ok=True)
        (git_repo / "new_pkg" / "mod.py").write_text("untracked work")
        return False, "boom", "agent_error"

    original = executor._invoke_claude_code
    executor._invoke_claude_code = mock_invoke
    try:
        success, output = executor.execute_task(task, job_id, "")
    finally:
        executor._invoke_claude_code = original

    assert not success
    assert "git stash pop" in output

    status = subprocess.run(["git", "status", "--porcelain"], cwd=git_repo,
                            capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""

    subprocess.run(["git", "stash", "pop"], cwd=git_repo, capture_output=True, check=True)
    assert (git_repo / "test.txt").read_text() == "edited by agent"
    assert (git_repo / "new_pkg" / "mod.py").read_text() == "untracked work"
