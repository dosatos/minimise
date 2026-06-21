import subprocess
from pathlib import Path
from typing import Optional, Tuple


class GitTracker:
    """Tracks git state and manages diff operations."""

    def __init__(self, repo_path: Path):
        """
        Initialize GitTracker with a repository path.

        Args:
            repo_path: Path to the git repository
        """
        self.repo_path = Path(repo_path)

    def validate_clean_state(self) -> Tuple[bool, str]:
        """
        Validate that the git repository has a clean state (no uncommitted changes).

        Returns:
            Tuple of (is_clean: bool, message: str)
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            if result.stdout.strip():
                # There are uncommitted changes
                return (False, f"Git repository has uncommitted changes:\n{result.stdout}")

            return (True, "Git repository is clean")

        except FileNotFoundError:
            return (False, "Git is not installed or not found in PATH")
        except subprocess.CalledProcessError as e:
            return (False, f"Git error: {e.stderr}")

    def get_current_commit(self) -> Optional[str]:
        """
        Get the current commit hash (SHA).

        Returns:
            The commit hash (40 hex characters) or None if error
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            commit_hash = result.stdout.strip()
            return commit_hash if len(commit_hash) == 40 else None

        except FileNotFoundError:
            return None
        except subprocess.CalledProcessError:
            return None

    def get_diff(self, base_commit: str) -> str:
        """
        Get the unified diff from a base commit to HEAD.

        Args:
            base_commit: The base commit hash to diff from

        Returns:
            The diff output as a string
        """
        try:
            result = subprocess.run(
                ["git", "diff", f"{base_commit}..HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            return result.stdout

        except FileNotFoundError:
            return ""
        except subprocess.CalledProcessError:
            return ""
