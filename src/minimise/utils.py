import subprocess
from pathlib import Path
from typing import Optional


def run_shell_command(command: str, cwd: Optional[Path] = None, timeout: int = 3600) -> tuple[bool, str]:
    """
    Execute a shell command and return success status and output.

    Args:
        command: Shell command to execute
        cwd: Working directory
        timeout: Timeout in seconds

    Returns:
        (success, output)
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def ensure_directory(path: Path) -> Path:
    """Ensure directory exists and return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
