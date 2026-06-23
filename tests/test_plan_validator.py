import textwrap

import pytest
import yaml
from pydantic import ValidationError

from minimise.models import Plan


def _valid_plan_dict():
    return {
        "name": "My Plan",
        "tasks": [
            {
                "id": "task-1",
                "name": "Task Name",
                "description": "A longer description with sufficient length",
                "goal": "A clear goal",
                "estimated_duration_min": 30,
            }
        ],
    }


class TestSyntaxValidation:
    """Plan.from_yaml / model_validate enforce basic schema."""

    def test_valid_plan_passes(self):
        plan = Plan.model_validate(_valid_plan_dict())
        assert plan.name == "My Plan"
        assert len(plan.tasks) == 1

    def test_missing_plan_name(self):
        with pytest.raises(ValidationError):
            Plan.model_validate({"tasks": _valid_plan_dict()["tasks"]})

    def test_missing_tasks_field(self):
        with pytest.raises(ValidationError):
            Plan.model_validate({"name": "My Plan"})

    def test_empty_tasks_list(self):
        with pytest.raises(ValidationError):
            Plan.model_validate({"name": "My Plan", "tasks": []})

    @pytest.mark.parametrize("missing", ["id", "name", "description", "goal", "estimated_duration_min"])
    def test_task_missing_required_field(self, missing):
        task = _valid_plan_dict()["tasks"][0]
        del task[missing]
        with pytest.raises(ValidationError):
            Plan.model_validate({"name": "P", "tasks": [task]})

    def test_duplicate_task_ids(self):
        p = _valid_plan_dict()
        p["tasks"].append(dict(p["tasks"][0], name="Task Two"))
        with pytest.raises(ValidationError, match="unique"):
            Plan.model_validate(p)


class TestEstimatedDurationValidation:
    """estimated_duration_min must be a positive integer."""

    def _plan(self, value):
        p = _valid_plan_dict()
        p["tasks"][0]["estimated_duration_min"] = value
        return p

    @pytest.mark.parametrize("bad", [0, -5, "soon", 3.5, True])
    def test_rejects_non_positive_int(self, bad):
        with pytest.raises(ValidationError):
            Plan.model_validate(self._plan(bad))

    def test_accepts_positive_int(self):
        assert Plan.model_validate(self._plan(5)).tasks[0].estimated_duration_min == 5


class TestFromYaml:
    """from_yaml reads the file, unwraps nested 'plan:', and preserves extras."""

    def _write(self, tmp_path, text):
        path = tmp_path / "plan.yaml"
        path.write_text(textwrap.dedent(text))
        return path

    def test_flat_format(self, tmp_path):
        path = self._write(tmp_path, yaml.dump(_valid_plan_dict()))
        plan = Plan.from_yaml(path)
        assert plan.name == "My Plan"

    def test_nested_plan_key_unwrapped(self, tmp_path):
        path = self._write(tmp_path, yaml.dump({"plan": _valid_plan_dict()}))
        plan = Plan.from_yaml(path)
        assert plan.name == "My Plan"

    def test_extras_preserved(self, tmp_path):
        data = _valid_plan_dict()
        data["briefing"] = "context here"
        data["tasks"][0]["pre_task_hook"] = "echo hi"
        path = self._write(tmp_path, yaml.dump(data))
        plan = Plan.from_yaml(path)
        assert plan.briefing == "context here"
        assert plan.tasks[0].pre_task_hook == "echo hi"

    def test_invalid_plan_raises(self, tmp_path):
        path = self._write(tmp_path, yaml.dump({"name": "P", "tasks": []}))
        with pytest.raises(ValidationError):
            Plan.from_yaml(path)
