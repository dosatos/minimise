"""Shared CLI helpers and the console.

The mutable config constants (DB_PATH / JOBS_DIR / REPO_PATH) live in the
package __init__ so that monkeypatching ``minimise.cli.DB_PATH`` (conftest) and
``importlib.reload(minimise.cli)`` (MINIMISE_HOME override test) keep working.
We read them lazily through ``_cli`` here so a patched value is always honored.
"""

import sqlite3
from pathlib import Path  # noqa: F401  (kept for parity / type ergonomics)

from rich.console import Console

from minimise.database import Database
from minimise.job_manager import JobManager
from minimise.git_tracker import GitTracker

import minimise.cli as _cli  # patchable constants live here; read at call time


console = Console()


def get_db() -> Database:
    """Get or create database instance."""
    db = Database(_cli.DB_PATH)
    db.init_db()
    return db


def get_job_manager(db: Database) -> JobManager:
    """Get job manager instance."""
    git_tracker = GitTracker(_cli.REPO_PATH)
    return JobManager(db, git_tracker, _cli.JOBS_DIR, _cli.REPO_PATH)


def resolve_job_id(job_id_or_prefix: str) -> str:
    """Resolve a job ID from full ID or prefix (e.g., first 8 chars)."""
    db = get_db()

    conn = sqlite3.connect(db.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM jobs WHERE id = ? OR id LIKE ?", (job_id_or_prefix, f"{job_id_or_prefix}%"))
    matches = cursor.fetchall()
    conn.close()

    if len(matches) == 1:
        return matches[0][0]
    elif len(matches) > 1:
        console.print(f"[red]Error: Multiple jobs match '{job_id_or_prefix}':[/red]")
        for match in matches:
            console.print(f"  {match[0]}")
        console.print("[yellow]Please provide more characters to disambiguate[/yellow]")
        raise SystemExit(1)
    else:
        console.print(f"[red]Error: Job '{job_id_or_prefix}' not found[/red]")
        raise SystemExit(1)


def _error_job_not_found(job_id: str):
    """Print the standard 'job not found' error and exit non-zero."""
    console.print(f"[red]Error: Job {job_id} not found[/red]")
    raise SystemExit(1)


def _format_datetime(dt, default: str = "N/A") -> str:
    """Format a datetime as 'YYYY-MM-DD HH:MM:SS', or return default if None."""
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else default


def _filter_tasks_by_id(tasks, task_id_str: str):
    """Filter tasks by full ID or prefix match."""
    return [t for t in tasks if t.id == task_id_str or t.id.startswith(task_id_str)]


def _get_and_validate_job(job_id: str):
    """Resolve a job ID/prefix, fetch the job, and exit with the standard
    'not found' error if it doesn't exist.

    Returns (resolved_job_id, db, job_obj).
    """
    job_id = resolve_job_id(job_id)
    db = get_db()
    job_obj = db.get_job(job_id)
    if job_obj is None:
        _error_job_not_found(job_id)
    return job_id, db, job_obj
