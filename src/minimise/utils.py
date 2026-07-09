import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional


def new_id(prefix: str) -> str:
    """Short prefixed id, e.g. new_id("job") -> "job-a1b2c3"."""
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def run_shell_command(command: str, cwd: Optional[Path] = None, timeout: Optional[int] = None,
                      env: Optional[dict] = None, stdin: Optional[str] = None) -> tuple[bool, str]:
    """Execute a shell command; return (success, combined stdout+stderr).

    env=None inherits the current process environment (today's behavior);
    pass a dict to run with a replaced environment (e.g. the target repo's venv).
    stdin, when a string, is written to the child process's stdin.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=stdin,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def project_env(repo_root: Path) -> Optional[dict]:
    """Environment for running a hook inside the TARGET repo's venv (fixes BUG-4).

    If the repo has a .venv/ (or venv/), prepend its bin to PATH and set
    VIRTUAL_ENV so `pytest`/`ruff`/etc. resolve to the project's, not minimise's.
    Returns None when no venv exists -> caller runs with inherited PATH.
    """
    repo_root = Path(repo_root)
    for name in (".venv", "venv"):
        venv = repo_root / name
        venv_bin = venv / "bin"
        if venv_bin.exists():
            return {
                **os.environ,
                "PATH": f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "VIRTUAL_ENV": str(venv),
            }
    return None


def ensure_directory(path: Path) -> Path:
    """Ensure directory exists and return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
