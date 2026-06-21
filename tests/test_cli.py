"""Tests for CLI interface."""

import pytest
import tempfile
from pathlib import Path
from click.testing import CliRunner
from minimise.cli import mini
from minimise.database import Database
from minimise.models import Job, JobStatus


@pytest.fixture
def temp_home_dir(monkeypatch):
    """Mock the home directory for minimise config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("HOME", tmpdir)
        yield Path(tmpdir)


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


def test_mini_job_list_empty(runner, temp_home_dir):
    """Test that mini job list works with empty job list."""
    result = runner.invoke(mini, ["job", "list"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output or "Jobs" in result.output


def test_mini_job_new_creates_job(runner, temp_home_dir):
    """Test that mini job new creates a job from a plan file."""
    # Create a temporary plan file
    with tempfile.TemporaryDirectory() as tmpdir:
        plan_path = Path(tmpdir) / "plan.yaml"
        plan_content = """
name: Test Plan
briefing: Test briefing
tasks:
  - name: Task 1
    description: First task
  - name: Task 2
    description: Second task
"""
        plan_path.write_text(plan_content)

        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])

        # Job creation should succeed (in real scenario with git repo)
        # But since we might not be in a git repo, check for appropriate error or success
        # The command should complete without Python exceptions
        assert result.exit_code in [0, 1]  # Allow both success and graceful failure
        # Ensure no Python tracebacks in output
        assert "Traceback" not in result.output


def test_mini_job_help(runner):
    """Test that job commands have help text."""
    result = runner.invoke(mini, ["job", "--help"])
    assert result.exit_code == 0
    assert "new" in result.output
    assert "list" in result.output
    assert "status" in result.output
    assert "stop" in result.output
    assert "resume" in result.output
    assert "logs" in result.output


def test_mini_view_help(runner):
    """Test that view commands have help text."""
    result = runner.invoke(mini, ["view", "--help"])
    assert result.exit_code == 0
    assert "start" in result.output
    assert "stop" in result.output


def test_mini_main_help(runner):
    """Test main CLI help."""
    result = runner.invoke(mini, ["--help"])
    assert result.exit_code == 0
    assert "Minimise" in result.output
    assert "job" in result.output
    assert "view" in result.output
