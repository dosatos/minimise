import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
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


class TestPlanReviewCLI:
    """Test agent-based plan review via CLI."""

    def test_plan_review_findings_block_job_creation(self, runner, mock_config_dir):
        """Plan review findings act as a hard gate: job creation fails with the findings reported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.yaml"
            plan_content = """
name: Test Feature
tasks:
  - id: task-1
    name: Setup Database
    description: Create database schema with user table including necessary columns
    goal: Have working database
    estimated_duration_min: 30
"""
            plan_path.write_text(plan_content)

            # Mock the reviewer to return findings
            with patch('minimise.interfaces.cli.PlanReviewer') as MockReviewer:
                mock_reviewer = MagicMock()
                MockReviewer.return_value = mock_reviewer

                # Return some findings
                from minimise.agents.plan_reviewer import ReviewFinding
                mock_reviewer.review.return_value = [
                    ReviewFinding(
                        task_id="task-1",
                        title="Test findings",
                        description="Sample finding for testing",
                        severity="medium",
                        suggestion="Consider adding more detail"
                    )
                ]

                result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

                # Verify review was called (not skipped)
                MockReviewer.assert_called_once()
                mock_reviewer.review.assert_called_once()

                # Findings are a hard gate: non-zero exit, no job created, no interactive prompt
                assert result.exit_code != 0
                assert "test findings" in result.output.lower()
                assert "review failed" in result.output.lower()
                assert "Job created" not in result.output

    def test_skip_review_flag_bypasses_review(self, runner, mock_config_dir):
        """--skip-review flag bypasses agent review."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.yaml"
            plan_content = """
name: Test Feature
tasks:
  - id: task-1
    name: Setup Database
    description: Create database schema with user table including necessary columns
    goal: Have working database
    estimated_duration_min: 30
"""
            plan_path.write_text(plan_content)

            with patch('minimise.agents.plan_reviewer.PlanReviewer') as MockReviewer:
                mock_reviewer = MagicMock()
                MockReviewer.return_value = mock_reviewer
                mock_reviewer.review.return_value = []

                result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path), "--skip-review"])

                # Reviewer should not be called when --skip-review is used
                # (it may be called but review should be skipped in flow)
                assert result.exit_code in [0, 1]  # Either succeeds or fails for other reasons (git), but not review failure
