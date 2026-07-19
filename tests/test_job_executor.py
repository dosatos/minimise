"""JobExecutor integration: the handoff written during task 1 flows to task 2.

The unit-level wiring lives in test_task_executor.py; this exercises the whole
loop with a real TaskExecutor + a stub harness, asserting JobExecutor forwards
execute_task's returned handoff (not a rebuilt-from-stdout one) to the next task.
"""

import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from minimise.agents.harness import AgentHarness, HarnessResult
from minimise.models import Job, JobStatus, Plan, Task, TaskStatus
from minimise.orchestration.hook_executor import HookExecutor
from minimise.orchestration.job_executor import JobExecutor
from minimise.orchestration.task_executor import TaskExecutor
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore


class _FixedHarnessFactory:
    """Test double: ignores the task, always returns the same harness."""

    def __init__(self, harness, model=None):
        self._harness = harness
        self._model = model

    def from_task(self, task):
        return self._harness

    def resolve_model(self, task):
        return self._model


@pytest.fixture
def git_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        for args in (["init"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
        (repo / "test.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        yield repo


class HandoffWritingHarness(AgentHarness):
    """Writes a per-task handoff file (task 1 only) and records each prompt."""

    def __init__(self):
        self.prompts = []

    def run(self, prompt, *, cwd=None, timeout=900, model=None, system_prompt=None,
            allow_edits=False, log_path=None, log_fields=None):
        self.prompts.append(prompt)
        # The prompt names the exact handoff path; task 1 writes a known handoff there.
        if len(self.prompts) == 1:
            path = re.search(r"to this exact path: (\S+)", prompt).group(1)
            Path(path).write_text("TASK1_AGENT_HANDOFF marker")
        return HarnessResult(success=True, output="stdout-noise", error=None)


def test_task1_agent_handoff_flows_to_task2(temp_db_dir, db, git_repo):
    git_tracker = GitTracker(git_repo)
    harness = HandoffWritingHarness()
    store = JobStore(db, temp_db_dir)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    t1 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
              name="T1", description="d1", status=TaskStatus.PENDING)
    t2 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
              name="T2", description="d2", status=TaskStatus.PENDING)
    db.create_task(t1)
    db.create_task(t2)
    job = Job(id=job_id, name="J", status=JobStatus.PENDING, tasks=[t1, t2])

    plan = Plan(name="J", tasks=[
        {"id": t1.id, "name": "T1", "description": "d1", "goal": "g1", "estimated_duration_min": 5},
        {"id": t2.id, "name": "T2", "description": "d2", "goal": "g2", "estimated_duration_min": 5},
    ])

    executor = JobExecutor(TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(harness)), HookExecutor())
    assert executor.execute(job, plan)

    # Task 2's prompt carries task 1's agent-written handoff, not the raw stdout.
    assert "TASK1_AGENT_HANDOFF marker" in harness.prompts[1]
    assert "(agent-written handoff)" in harness.prompts[1]
    assert "stdout-noise" not in harness.prompts[1]


def _single_task_job(db, git_tracker, *, pre=None, post=None, plan_pre=None):
    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    t1 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
              name="T1", description="d1", status=TaskStatus.PENDING)
    db.create_task(t1)
    job = Job(id=job_id, name="J", status=JobStatus.PENDING, tasks=[t1])
    plan = Plan(name="J", pre_hooks=plan_pre or [], tasks=[{
        "id": t1.id, "name": "T1", "description": "d1", "goal": "g1",
        "estimated_duration_min": 5, "pre_hooks": pre or [], "post_hooks": post or [],
    }])
    return job, plan, t1


def test_post_task_hook_failure_fails_task(temp_db_dir, db, git_repo):
    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    job, plan, t1 = _single_task_job(
        db, git_tracker,
        post=[{"name": "check", "shell": "exit 1", "estimated_duration_min": 1}])

    executor = JobExecutor(
        TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(HandoffWritingHarness())), HookExecutor())
    assert executor.execute(job, plan) is False
    failed = db.get_task(t1.id)
    assert failed.status == TaskStatus.FAILED
    # Failure detail now lives in job.log (the sole narration store), not task.output.
    log = (temp_db_dir / job.id / "job.log").read_text()
    assert "Post-task hook failed" in log


def test_post_task_hook_retry_reruns_with_findings(temp_db_dir, db, git_repo, tmp_path):
    """on_failure=retry: hook fails first attempt, agent re-runs seeing findings."""
    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    # A hook that fails while a sentinel file is absent, then passes once present.
    flag = tmp_path / "fixed"
    job, plan, t1 = _single_task_job(
        db, git_tracker,
        post=[{"name": "gate", "shell": f"test -f {flag} && echo PASS || (echo NEEDS_FIX; exit 1)",
               "estimated_duration_min": 1, "on_failure": "retry"}])

    class FixOnRetryHarness(HandoffWritingHarness):
        def run(self, prompt, **kw):
            r = super().run(prompt, **kw)
            if len(self.prompts) >= 2:  # second attempt "fixes" it
                flag.write_text("done")
            return r

    harness = FixOnRetryHarness()
    executor = JobExecutor(TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(harness)), HookExecutor())
    assert executor.execute(job, plan) is True
    assert len(harness.prompts) == 2  # re-ran once
    assert "NEEDS_FIX" in harness.prompts[1]  # findings fed into retry
    assert "Post-task review findings" in harness.prompts[1]
    # The agent DID write a handoff on attempt 0, so the findings ride the real
    # agent handoff (prepended), NOT _read_handoff's empty-handoff fallback —
    # if they only survived via the fallback, this invariant would be masked.
    assert "TASK1_AGENT_HANDOFF marker" in harness.prompts[1]
    assert "(agent-written handoff)" in harness.prompts[1]
    assert db.get_task(t1.id).status == TaskStatus.COMPLETED


def test_post_task_hook_skip_never_blocks(temp_db_dir, db, git_repo):
    """on_failure=skip: nonzero hook is recorded but the task still completes."""
    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    job, plan, t1 = _single_task_job(
        db, git_tracker,
        post=[{"name": "flaky", "shell": "exit 1", "estimated_duration_min": 1,
               "on_failure": "skip"}])

    harness = HandoffWritingHarness()
    # Store-backed HookExecutor so the failing hook is persisted as an Execution.
    hooks = HookExecutor(store=store, job_id=job.id, repo_root=git_repo)
    executor = JobExecutor(TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(harness)), hooks)
    assert executor.execute(job, plan) is True
    assert len(harness.prompts) == 1  # no retry
    assert db.get_task(t1.id).status == TaskStatus.COMPLETED
    # The skipped hook still ran and was recorded, marked FAILED but non-blocking.
    flaky = [e for e in db.list_executions_for_task(t1.id) if e.hook_name == "flaky"]
    assert len(flaky) == 1 and flaky[0].status == TaskStatus.FAILED


def test_pre_task_hook_failure_skips_task(temp_db_dir, db, git_repo):
    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    job, plan, t1 = _single_task_job(
        db, git_tracker,
        pre=[{"name": "check", "shell": "exit 1", "estimated_duration_min": 1}])

    harness = HandoffWritingHarness()
    executor = JobExecutor(TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(harness)), HookExecutor())
    assert executor.execute(job, plan) is False
    assert harness.prompts == []  # task never ran
    failed = db.get_task(t1.id)
    assert failed.status == TaskStatus.FAILED


def test_pre_plan_hook_receives_plan_yaml_on_stdin(temp_db_dir, db, git_repo, tmp_path):
    """The whole point of the branch: a pre_plan hook can review the plan on stdin."""
    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    captured = tmp_path / "captured.yaml"
    job, plan, _ = _single_task_job(db, git_tracker, plan_pre=[
        {"name": "review", "shell": f"cat > {captured}", "estimated_duration_min": 1}])

    executor = JobExecutor(
        TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(HandoffWritingHarness())), HookExecutor())
    assert executor.execute(job, plan)

    content = captured.read_text()
    assert "name: J" in content       # plan name reached the hook
    assert "T1" in content            # ...along with its tasks


def test_pre_plan_hook_failure_aborts_before_any_task(temp_db_dir, db, git_repo):
    """A nonzero pre_plan hook aborts the run before any task executes."""
    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    job, plan, _ = _single_task_job(db, git_tracker, plan_pre=[
        {"name": "reject", "shell": "exit 1", "estimated_duration_min": 1}])

    harness = HandoffWritingHarness()
    executor = JobExecutor(TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(harness)), HookExecutor())
    assert executor.execute(job, plan) is False
    assert harness.prompts == []  # no task ran


def test_resume_skips_completed_and_seeds_prev_handoff(temp_db_dir, db, git_repo):
    """A COMPLETED task 1 is skipped; task 2 runs first and receives task 1's
    persisted handoff — not the empty in-memory seed."""
    git_tracker = GitTracker(git_repo)
    harness = HandoffWritingHarness()
    store = JobStore(db, temp_db_dir)

    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    t1 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
              name="T1", description="d1", status=TaskStatus.COMPLETED, retries=0)
    t2 = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
              name="T2", description="d2", status=TaskStatus.PENDING)
    db.create_task(t1)
    db.create_task(t2)
    # Task 1's handoff already on disk from its prior successful run.
    store.handoff_path(job_id, t1.id, 0).write_text("PRIOR_RUN_HANDOFF marker")
    job = Job(id=job_id, name="J", status=JobStatus.PENDING, tasks=[t1, t2])
    plan = Plan(name="J", tasks=[
        {"id": t1.id, "name": "T1", "description": "d1", "goal": "g1", "estimated_duration_min": 5},
        {"id": t2.id, "name": "T2", "description": "d2", "goal": "g2", "estimated_duration_min": 5},
    ])

    executor = JobExecutor(TaskExecutor(store, git_tracker, factory=_FixedHarnessFactory(harness)), HookExecutor())
    assert executor.execute(job, plan)

    # Only task 2 ran, and it got task 1's persisted handoff.
    assert len(harness.prompts) == 1
    assert "d2" in harness.prompts[0]
    assert "PRIOR_RUN_HANDOFF marker" in harness.prompts[0]


def test_mark_running_clears_stale_completed_at(temp_db_dir, db, git_repo):
    """Re-running a FAILED task (attempt 0) clears its stale completed_at so
    duration math never goes negative."""
    from datetime import datetime, timedelta

    git_tracker = GitTracker(git_repo)
    store = JobStore(db, temp_db_dir)
    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="J", status=JobStatus.PENDING,
                      base_commit=git_tracker.get_current_commit()))
    task = Task(estimated_duration_min=5, id=str(uuid.uuid4()), job_id=job_id,
                name="T1", description="d1", status=TaskStatus.PENDING)
    db.create_task(task)
    # Simulate a prior FAILED run leaving a stale completed_at behind.
    db.update_task_status(task.id, TaskStatus.FAILED,
                          completed_at=datetime.utcnow() - timedelta(hours=1))
    assert db.get_task(task.id).completed_at is not None

    store.mark_running(task, 0)
    reloaded = db.get_task(task.id)
    assert reloaded.completed_at is None  # cleared → no negative duration
