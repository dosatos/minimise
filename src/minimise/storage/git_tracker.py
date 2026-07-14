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

    def _run(self, args: list) -> Optional[str]:
        """Run a git command, returning its stdout, or None if git is missing
        or the command exits non-zero."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

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
        out = self._run(["rev-parse", "HEAD"])
        if out is None:
            return None
        commit_hash = out.strip()
        return commit_hash if len(commit_hash) == 40 else None

    def get_diff(self, base_commit: str) -> str:
        """
        Get the unified diff from a base commit to HEAD.

        Args:
            base_commit: The base commit hash to diff from

        Returns:
            The diff output as a string
        """
        out = self._run(["diff", f"{base_commit}..HEAD"])
        return out if out is not None else ""

    def commit(self, message: str) -> Optional[str]:
        """
        Create a git commit with the given message if there are staged/unstaged changes.

        Args:
            message: The commit message

        Returns:
            The commit hash if successful, None if no changes to commit or error
        """
        try:
            # First stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            # Check if there are changes to commit
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            if not result.stdout.strip():
                # No changes to commit
                return None

            # Create the commit
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            # Get the new commit hash
            commit_hash = self.get_current_commit()
            return commit_hash

        except FileNotFoundError:
            return None
        except subprocess.CalledProcessError as e:
            # Commit may have failed due to no changes or other issues
            return None

    def stash(self, message: str) -> bool:
        """
        Stash uncommitted work, including untracked files.

        Args:
            message: The stash message

        Returns:
            True if a stash was created, False if nothing to stash or error
        """
        try:
            result = subprocess.run(
                ["git", "stash", "push", "-u", "-m", message],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            # "No local changes to save" => no-op, not an error.
            return "No local changes to save" not in result.stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
