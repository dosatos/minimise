import pytest
from minimise.plan_validator import PlanValidator, ValidationIssue, ValidationLevel


@pytest.fixture
def validator():
    return PlanValidator()


class TestSyntaxValidation:
    """Test basic YAML/schema syntax validation only."""

    def test_valid_plan_passes_syntax(self, validator):
        """Valid plan with all required fields passes."""
        plan = {
            "name": "My Plan",
            "tasks": [
                {
                    "id": "task-1",
                    "name": "Task Name",
                    "description": "A longer description with sufficient length",
                    "goal": "A clear goal",
                    "estimated_duration_min": 30
                }
            ]
        }
        errors = validator.validate(plan)
        assert len(errors) == 0

    def test_missing_plan_name(self, validator):
        """Plan without name field fails."""
        plan = {"description": "Some plan", "tasks": []}
        errors = validator.validate(plan)
        assert len(errors) > 0
        assert any("name" in e.message.lower() for e in errors)

    def test_missing_tasks_field(self, validator):
        """Plan without tasks field fails."""
        plan = {"name": "My Plan", "description": "Some plan"}
        errors = validator.validate(plan)
        assert len(errors) > 0
        assert any("tasks" in e.message.lower() for e in errors)

    def test_empty_tasks_list(self, validator):
        """Plan with empty tasks list fails."""
        plan = {"name": "My Plan", "description": "Some plan", "tasks": []}
        errors = validator.validate(plan)
        assert len(errors) > 0

    def test_task_missing_id(self, validator):
        """Task without id field fails."""
        plan = {
            "name": "My Plan",
            "tasks": [{"name": "Task 1", "description": "Desc"}]
        }
        errors = validator.validate(plan)
        assert len(errors) > 0

    def test_task_missing_name(self, validator):
        """Task without name field fails."""
        plan = {
            "name": "My Plan",
            "tasks": [{"id": "task-1", "description": "Desc"}]
        }
        errors = validator.validate(plan)
        assert len(errors) > 0

    def test_task_missing_description(self, validator):
        """Task without description field fails."""
        plan = {
            "name": "My Plan",
            "tasks": [{"id": "task-1", "name": "Task Name"}]
        }
        errors = validator.validate(plan)
        assert len(errors) > 0

    def test_task_missing_goal(self, validator):
        """Task without goal field fails."""
        plan = {
            "name": "My Plan",
            "tasks": [
                {
                    "id": "task-1",
                    "name": "Task Name",
                    "description": "A description"
                }
            ]
        }
        errors = validator.validate(plan)
        assert len(errors) > 0

    def test_task_missing_estimated_duration(self, validator):
        """Task without estimated_duration_min field fails."""
        plan = {
            "name": "My Plan",
            "tasks": [
                {
                    "id": "task-1",
                    "name": "Task Name",
                    "description": "A description",
                    "goal": "A goal"
                }
            ]
        }
        errors = validator.validate(plan)
        assert len(errors) > 0

    def test_duplicate_task_ids(self, validator):
        """Plan with duplicate task IDs fails."""
        plan = {
            "name": "My Plan",
            "tasks": [
                {
                    "id": "task-1",
                    "name": "Task One",
                    "description": "Desc one",
                    "goal": "Goal one",
                    "estimated_duration_min": 30
                },
                {
                    "id": "task-1",
                    "name": "Task Two",
                    "description": "Desc two",
                    "goal": "Goal two",
                    "estimated_duration_min": 30
                }
            ]
        }
        errors = validator.validate(plan)
        assert len(errors) > 0
        assert any("duplicate" in e.message.lower() or "unique" in e.message.lower() for e in errors)

    def test_validation_issue_structure(self, validator):
        """Validation issues have required fields."""
        plan = {"name": "Bad Plan", "tasks": []}
        errors = validator.validate(plan)
        assert len(errors) > 0

        for issue in errors:
            assert hasattr(issue, 'level')
            assert hasattr(issue, 'field')
            assert hasattr(issue, 'message')
            assert issue.level == ValidationLevel.ERROR
