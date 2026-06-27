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

    def run(self, prompt, *, cwd=None, timeout=900, model=None, allow_edits=False, log_path=None):
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

    executor = JobExecutor(TaskExecutor(store, git_tracker, harness=harness), HookExecutor(), git_tracker)
    assert executor.execute(job, plan)

    # Task 2's prompt carries task 1's agent-written handoff, not the raw stdout.
    assert "TASK1_AGENT_HANDOFF marker" in harness.prompts[1]
    assert "(agent-written handoff)" in harness.prompts[1]
    assert "stdout-noise" not in harness.prompts[1]
