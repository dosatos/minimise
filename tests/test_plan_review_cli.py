import pytest
import tempfile
from pathlib import Path
from click.testing import CliRunner
from minimise.interfaces.cli import mini
from minimise.storage.database import Database


@pytest.fixture
def runner():
    return CliRunner()


class TestPlanValidationCLI:
    """Test plan validation via CLI (syntax checks)."""

    def test_plan_with_missing_fields_fails_syntax_check(self, runner, mock_config_dir):
        """Plan missing required fields fails immediately with syntax error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.yaml"
            plan_content = """
name: Bad Plan
tasks:
  - id: task-1
    name: Task One
"""
            plan_path.write_text(plan_content)

            result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

            assert result.exit_code != 0
            assert "syntax validation failed" in result.output.lower() or "description" in result.output.lower()

    def test_plan_with_duplicate_ids_fails_syntax_check(self, runner, mock_config_dir):
        """Plan with duplicate task IDs fails syntax check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.yaml"
            plan_content = """
name: Duplicate ID Plan
tasks:
  - id: task-1
    name: Task One
    description: First task with sufficient description length
    goal: Clear goal
    estimated_duration_min: 30
  - id: task-1
    name: Task Two
    description: Second task with sufficient description length
    goal: Another goal
    estimated_duration_min: 30
"""
            plan_path.write_text(plan_content)

            result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

            assert result.exit_code != 0
            assert "syntax validation failed" in result.output.lower() or "duplicate" in result.output.lower()

    def test_syntax_validation_passes_for_valid_plan(self, runner, mock_config_dir):
        """Valid plan passes syntax validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.yaml"
            plan_content = """
name: Valid Plan
tasks:
  - id: task-1
    name: Task One
    description: First task with sufficient description length
    goal: Clear goal
    estimated_duration_min: 30
"""
            plan_path.write_text(plan_content)

            result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

            # Should at least pass syntax validation
            assert "syntax validation failed" not in result.output.lower()
            assert "✓" in result.output  # Check mark for validation passed
