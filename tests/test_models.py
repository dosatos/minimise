from datetime import datetime

import pytest
from minimise.models import Job, JobStatus, Task, TaskStatus


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
