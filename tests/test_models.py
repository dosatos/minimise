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
        "retries": 2,
        "created_at": "2026-01-02T03:04:05",
        "started_at": "2026-01-02T03:04:05",
        "completed_at": None,
        "diff_path": "/tmp/diff",
        "assignee": None,
    }


def test_task_to_dict_carries_assignee():
    default = Task(id="t1", job_id="j1", name="n", description="d", estimated_duration_min=5)
    assert default.to_dict()["assignee"] is None
    assigned = Task(id="t1", job_id="j1", name="n", description="d",
                    estimated_duration_min=5, assignee="coder")
    assert assigned.to_dict()["assignee"] == "coder"


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


def test_execution_id_format():
    """Readable {key}#{value} segments; a segment present only when meaningful."""
    from minimise.models import Execution
    attempt = Execution(task_id="task-9f", attempt=1, job_id="job-ab12")
    task_hook = Execution(task_id="task-9f", attempt=0, job_id="job-ab12",
                          execution_type="post_task", hook_name="pytest")
    plan_hook = Execution(task_id=None, attempt=0, job_id="job-ab12",
                          execution_type="post_plan", hook_name="deploy")

    assert attempt.execution_id == "job#job-ab12#task#task-9f#attempt#1"
    assert task_hook.execution_id == "job#job-ab12#task#task-9f#post_task_hook#pytest"
    assert plan_hook.execution_id == "job#job-ab12#post_plan_hook#deploy"


def test_execution_id_deterministic_and_distinct():
    from minimise.models import Execution
    a = Execution(task_id="t1", attempt=2, job_id="j1")
    assert a.execution_id == Execution(task_id="t1", attempt=2, job_id="j1").execution_id
    h1 = Execution(task_id="t1", attempt=0, job_id="j1", execution_type="post_task", hook_name="ruff")
    h2 = Execution(task_id="t1", attempt=0, job_id="j1", execution_type="post_task", hook_name="pytest")
    assert h1.execution_id != h2.execution_id  # named hooks no longer collide


def test_hook_dataclass_shape():
    from minimise.models import Hook
    script = Hook(name="Run tests", estimated_duration_min=3, shell="pytest -q")
    ref = Hook(name="security", estimated_duration_min=5)
    assert script.shell == "pytest -q"
    assert ref.shell is None


def test_execution_to_dict_has_hook_name():
    from minimise.models import Execution
    d = Execution(task_id="t1", attempt=0, job_id="j1",
                  execution_type="post_task", hook_name="pytest").to_dict()
    assert d["hook_name"] == "pytest"
    assert d["execution_id"].endswith("post_task_hook#pytest")


def test_execution_to_dict_carries_new_fields():
    e = Execution(task_id="t1", attempt=1, job_id="j1", execution_type="task")
    d = e.to_dict()
    assert d["job_id"] == "j1"
    assert d["execution_type"] == "task"
    assert d["execution_id"] == e.execution_id
    assert d["task_id"] == "t1"
    assert d["attempt"] == 1


LOOP_YAML = """\
version: "0.0.1"
name: "sample loop"
goal: "make it good"
loop:
  plan:
    persona: planner
  implement:
    persona: coder
  evaluate:
    max_concurrent: 2
    dimensions:
      - name: coverage
        persona: cov-eval
        rubric: "cover the edge cases"
      - name: correctness
        prompt: "You are a correctness evaluator"
        rubric: "no bugs"
max_iterations: 10
"""


def test_loopspec_from_yaml(tmp_path):
    from minimise.models import LoopSpec
    p = tmp_path / "loop.yaml"
    p.write_text(LOOP_YAML)
    spec = LoopSpec.from_yaml(p)
    assert spec.version == "0.0.1"
    assert spec.max_iterations == 10
    assert spec.loop.plan.persona == "planner"
    assert spec.loop.evaluate.max_concurrent == 2
    assert [d.name for d in spec.loop.evaluate.dimensions] == ["coverage", "correctness"]
    assert spec.loop.evaluate.dimensions[1].prompt.startswith("You are")


def test_dimension_rejects_two_workers():
    from minimise.models import Dimension
    with pytest.raises(ValueError):
        Dimension(name="coverage", rubric="r", persona="p", prompt="q")


def test_loopspec_plan_version_defaults_to_one(tmp_path):
    from minimise.models import LoopSpec
    p = tmp_path / "loop.yaml"
    p.write_text(LOOP_YAML)
    spec = LoopSpec.from_yaml(p)
    assert spec.plan_version == 1


def test_loopspec_plan_version_round_trips(tmp_path):
    from minimise.models import LoopSpec
    p = tmp_path / "loop.yaml"
    p.write_text(LOOP_YAML + "plan_version: 3\n")
    spec = LoopSpec.from_yaml(p)
    assert spec.plan_version == 3


def test_evaluate_config_rejects_duplicate_dimension_names():
    from minimise.models import EvaluateConfig
    with pytest.raises(ValueError):
        EvaluateConfig(
            dimensions=[
                {"name": "coverage", "rubric": "r1"},
                {"name": "coverage", "rubric": "r2"},
            ]
        )


def test_evaluate_config_accepts_distinct_dimension_names():
    from minimise.models import EvaluateConfig
    cfg = EvaluateConfig(
        dimensions=[
            {"name": "coverage", "rubric": "r1"},
            {"name": "correctness", "rubric": "r2"},
        ]
    )
    assert [d.name for d in cfg.dimensions] == ["coverage", "correctness"]


def test_timeout_min_below_estimate_rejected():
    from minimise.models import Hook, PlanTask
    with pytest.raises(ValueError, match=r"timeout_min \(3\) must be >= estimated_duration_min \(5\)"):
        Hook(name="h", shell="true", estimated_duration_min=5, timeout_min=3)
    with pytest.raises(ValueError, match="timeout_min"):
        PlanTask(id="t1", name="N", description="d", goal="g",
                 estimated_duration_min=5, timeout_min=3)


def test_timeout_min_equal_or_above_estimate_accepted():
    from minimise.models import Hook, PlanTask
    assert Hook(name="h", shell="true", estimated_duration_min=5, timeout_min=5).timeout_min == 5
    assert PlanTask(id="t1", name="N", description="d", goal="g",
                    estimated_duration_min=5, timeout_min=9).timeout_min == 9


def test_timeout_min_omitted_accepted():
    from minimise.models import Hook, PlanTask
    assert Hook(name="h", shell="true", estimated_duration_min=5).timeout_min is None
    assert PlanTask(id="t1", name="N", description="d", goal="g",
                    estimated_duration_min=5).timeout_min is None
