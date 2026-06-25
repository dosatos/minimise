from datetime import datetime

import pytest
from minimise.models import Execution, Job, JobStatus, Task, TaskStatus


def test_task_requires_estimated_duration_min():
    with pytest.raises(TypeError):
        Task(id="t1", job_id="j1", name="n", description="d")


def test_task_accepts_estimated_duration_min():
    t = Task(id="t1", job_id="j1", name="n", description="d", estimated_duration_min=5)
    assert t.estimated_duration_min == 5


def test_task_to_dict():
    created = datetime(2026, 1, 2, 3, 4, 5)
    t = Task(
        id="t1",
        job_id="j1",
        name="n",
        description="d",
        estimated_duration_min=5,
        status=TaskStatus.RUNNING,
        output="out",
        retries=2,
        created_at=created,
        started_at=created,
        completed_at=None,
        diff_path="/tmp/diff",
    )
    assert t.to_dict() == {
        "id": "t1",
        "job_id": "j1",
        "name": "n",
        "description": "d",
        "status": "running",
        "output": "out",
        "retries": 2,
        "created_at": "2026-01-02T03:04:05",
        "started_at": "2026-01-02T03:04:05",
        "completed_at": None,
        "diff_path": "/tmp/diff",
    }


def test_job_to_dict_with_nested_tasks():
    created = datetime(2026, 1, 2, 3, 4, 5)
    t = Task(id="t1", job_id="j1", name="n", description="d", estimated_duration_min=5,
             created_at=created)
    j = Job(
        id="j1",
        name="job",
        status=JobStatus.COMPLETED,
        plan_path="plan.yaml",
        base_commit=None,
        created_at=created,
        started_at=None,
        completed_at=created,
        tasks=[t],
    )
    d = j.to_dict()
    assert d["id"] == "j1"
    assert d["status"] == "completed"
    assert d["base_commit"] is None
    assert d["created_at"] == "2026-01-02T03:04:05"
    assert d["started_at"] is None
    assert d["completed_at"] == "2026-01-02T03:04:05"
    assert d["tasks"] == [t.to_dict()]


def test_job_to_dict_empty_tasks():
    j = Job(id="j1", name="job")
    assert j.to_dict()["tasks"] == []


def test_execution_id_derivation():
    """execution_id is deterministic and distinguishes the three run shapes."""
    task_attempt = Execution(task_id="t1", attempt=2, job_id="j1")
    plan_hook = Execution(task_id=None, attempt=0, job_id="j1", execution_type="pre_plan")
    per_task_hook = Execution(task_id="t1", attempt=0, job_id="j1", execution_type="pre_task")

    # task default type
    assert task_attempt.execution_type == "task"
    # opaque but deterministic: same logical run -> same id
    assert task_attempt.execution_id == Execution(task_id="t1", attempt=2, job_id="j1").execution_id
    # the three shapes are distinct
    ids = {task_attempt.execution_id, plan_hook.execution_id, per_task_hook.execution_id}
    assert len(ids) == 3
    # plan hook has no task_id
    assert plan_hook.task_id is None


def test_execution_to_dict_carries_new_fields():
    e = Execution(task_id="t1", attempt=1, job_id="j1", execution_type="task")
    d = e.to_dict()
    assert d["job_id"] == "j1"
    assert d["execution_type"] == "task"
    assert d["execution_id"] == e.execution_id
    assert d["task_id"] == "t1"
    assert d["attempt"] == 1
