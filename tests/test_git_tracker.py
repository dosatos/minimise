import pytest
import tempfile
import subprocess
from pathlib import Path
from minimise.storage.git_tracker import GitTracker


@pytest.fixture
def git_repo():
    """Create a temporary git repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Initialize git repository
        subprocess.run(
            ["git", "init"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )

        # Configure git user
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )

        # Create initial commit
        test_file = repo_path / "test.txt"
        test_file.write_text("initial content")

        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )

        yield repo_path


def test_validate_clean_state_clean(git_repo):
    """Test that validate_clean_state detects a clean repository."""
    tracker = GitTracker(git_repo)
    is_clean, message = tracker.validate_clean_state()

    assert is_clean is True
    assert isinstance(message, str)


def test_validate_clean_state_dirty(git_repo):
    """Test that validate_clean_state detects uncommitted changes."""
    # Modify a file
    test_file = git_repo / "test.txt"
    test_file.write_text("modified content")

    tracker = GitTracker(git_repo)
    is_clean, message = tracker.validate_clean_state()

    assert is_clean is False
    assert isinstance(message, str)


def test_get_current_commit(git_repo):
    """Test that get_current_commit returns a valid commit hash."""
    tracker = GitTracker(git_repo)
    commit = tracker.get_current_commit()

    assert commit is not None
    assert len(commit) == 40
    assert all(c in "0123456789abcdef" for c in commit)


def test_get_diff(git_repo):
    """Test that get_diff returns unified diff between commits."""
    tracker = GitTracker(git_repo)

    # Get initial commit hash
    base_commit = tracker.get_current_commit()

    # Create a new file
    new_file = git_repo / "new_file.txt"
    new_file.write_text("new content")

    subprocess.run(
        ["git", "add", "new_file.txt"],
        cwd=git_repo,
        capture_output=True,
        check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Add new file"],
        cwd=git_repo,
        capture_output=True,
        check=True
    )

    # Get diff
    diff = tracker.get_diff(base_commit)

    assert isinstance(diff, str)
    assert len(diff) > 0
    assert "new_file.txt" in diff
