"""Plan validation - basic syntax checks only. Quality review handled by agent."""

from enum import Enum
from typing import List, Optional


class ValidationLevel(Enum):
    """Severity level for validation issues."""
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue:
    """Represents a single validation issue."""

    def __init__(
        self,
        level: ValidationLevel,
        field: str,
        message: str,
        suggestion: Optional[str] = None,
    ):
        self.level = level
        self.field = field
        self.message = message
        self.suggestion = suggestion

    def __repr__(self):
        return f"ValidationIssue({self.level.value}, {self.field}, {self.message})"


class PlanValidator:
    """Validates job plans - basic syntax/schema only. Quality review by agent."""

    def validate(self, plan: dict) -> List[ValidationIssue]:
        """
        Validate a plan dictionary for basic syntax/schema.

        Args:
            plan: Plan dictionary to validate

        Returns:
            List of ValidationIssue objects. Empty list means plan is valid.
        """
        issues = []

        # Check required plan fields
        if "name" not in plan:
            issues.append(ValidationIssue(
                ValidationLevel.ERROR,
                "plan.name",
                "Plan must have a 'name' field"
            ))

        if "tasks" not in plan:
            issues.append(ValidationIssue(
                ValidationLevel.ERROR,
                "plan.tasks",
                "Plan must have a 'tasks' field with at least one task"
            ))
            return issues

        # Check tasks is not empty
        if not plan["tasks"] or len(plan["tasks"]) == 0:
            issues.append(ValidationIssue(
                ValidationLevel.ERROR,
                "plan.tasks",
                "Plan must have at least one task"
            ))
            return issues

        # Check each task has required fields
        seen_ids = set()
        for i, task in enumerate(plan["tasks"]):
            if "id" not in task:
                issues.append(ValidationIssue(
                    ValidationLevel.ERROR,
                    f"task[{i}].id",
                    f"Task {i} must have an 'id' field"
                ))

            if "name" not in task:
                issues.append(ValidationIssue(
                    ValidationLevel.ERROR,
                    f"task[{i}].name",
                    f"Task {i} must have a 'name' field"
                ))

            if "description" not in task:
                issues.append(ValidationIssue(
                    ValidationLevel.ERROR,
                    f"task[{i}].description",
                    f"Task {i} must have a 'description' field"
                ))

            if "goal" not in task:
                issues.append(ValidationIssue(
                    ValidationLevel.ERROR,
                    f"task[{i}].goal",
                    f"Task {i} must have a 'goal' field"
                ))

            if "estimated_duration_min" not in task:
                issues.append(ValidationIssue(
                    ValidationLevel.ERROR,
                    f"task[{i}].estimated_duration_min",
                    f"Task {i} must have an 'estimated_duration_min' field"
                ))

            # Check for duplicate IDs
            if "id" in task:
                task_id = task["id"]
                if task_id in seen_ids:
                    issues.append(ValidationIssue(
                        ValidationLevel.ERROR,
                        f"task[{i}].id",
                        f"Duplicate task ID '{task_id}'. Task IDs must be unique."
                    ))
                seen_ids.add(task_id)

        return issues
