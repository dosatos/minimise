"""Shared CLI helpers and the console.

The mutable config constants (DB_PATH / JOBS_DIR / REPO_PATH) live in the
package __init__ so that monkeypatching ``minimise.interfaces.cli.DB_PATH`` (conftest) and
``importlib.reload(minimise.interfaces.cli)`` (MINIMISE_HOME override test) keep working.
We read them lazily through ``_cli`` here so a patched value is always honored.
"""

import json
import sqlite3
from pathlib import Path  # noqa: F401  (kept for parity / type ergonomics)

from rich.console import Console

from minimise.storage.database import Database
from minimise.orchestration.job_controller import JobController

import minimise.interfaces.cli as _cli  # patchable constants live here; read at call time


console = Console()


def get_db() -> Database:
    """Get or create database instance."""
    db = Database(_cli.DB_PATH)
    db.init_db()
    return db


def get_job_controller(db: Database) -> JobController:
    """Get job controller instance."""
    from minimise.personas import load_personas
    personas = load_personas(_cli.CONFIG_DIR)
    return JobController.from_paths(db, _cli.REPO_PATH, _cli.JOBS_DIR, personas=personas)


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


def resolve_loop_id(loop_id_or_prefix: str) -> str:
    """Resolve a loop ID from full ID or prefix (mirrors resolve_job_id)."""
    db = get_db()

    conn = sqlite3.connect(db.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT loop_id FROM loops WHERE loop_id = ? OR loop_id LIKE ?",
                   (loop_id_or_prefix, f"{loop_id_or_prefix}%"))
    matches = cursor.fetchall()
    conn.close()

    if len(matches) == 1:
        return matches[0][0]
    elif len(matches) > 1:
        console.print(f"[red]Error: Multiple loops match '{loop_id_or_prefix}':[/red]")
        for match in matches:
            console.print(f"  {match[0]}")
        console.print("[yellow]Please provide more characters to disambiguate[/yellow]")
        raise SystemExit(1)
    else:
        console.print(f"[red]Error: Loop '{loop_id_or_prefix}' not found[/red]")
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


def task_narration(job_id: str, task) -> str:
    """Agent narration for display: reconstructed from job.log records tagged
    with this task's step (job.log is the sole narration store).
    Missing/partial log returns "" — this is a display fallback, not a query."""
    log_path = _cli.JOBS_DIR / job_id / "job.log"
    try:
        lines = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("type") == "task" and rec.get("step", "").split("  · try")[0].strip() == task.name:
                lines.append(rec.get("message", ""))
        return "\n".join(lines)
    except Exception:
        return ""


def _get_and_validate_job(job_id: str):
    """Resolve a job ID/prefix, fetch the job, and exit with the standard
    'not found' error if it doesn't exist.

    Returns (resolved_job_id, db, job_obj).
    """
    job_id = resolve_job_id(job_id)
    db = get_db()
    job_obj = get_job_controller(db).store.load(job_id)
    if job_obj is None:
        _error_job_not_found(job_id)
    return job_id, db, job_obj
