import pytest
from minimise.models import Task


def test_task_requires_estimated_duration_min():
    with pytest.raises(TypeError):
        Task(id="t1", job_id="j1", name="n", description="d")


def test_task_accepts_estimated_duration_min():
    t = Task(id="t1", job_id="j1", name="n", description="d", estimated_duration_min=5)
    assert t.estimated_duration_min == 5
