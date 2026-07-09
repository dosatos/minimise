"""CLI interface for Minimise - plan orchestrator for multi-agent execution.

The package __init__ owns the config constants (CONFIG_DIR / DB_PATH / JOBS_DIR /
REPO_PATH) so that ``monkeypatch.setattr("minimise.interfaces.cli.DB_PATH", ...)`` (conftest)
and ``importlib.reload(minimise.interfaces.cli)`` (MINIMISE_HOME override test) keep working.
Command modules read them lazily through ``import minimise.interfaces.cli as _cli`` so a
patched/reloaded value is always honored.
"""

import os
from pathlib import Path

import click

# Global constants (MINIMISE_HOME overrides the default ~/.minimise location)
CONFIG_DIR = Path(os.environ.get("MINIMISE_HOME") or Path.home() / ".minimise")
DB_PATH = CONFIG_DIR / "minimise.db"
JOBS_DIR = CONFIG_DIR / "jobs"
REPO_PATH = Path.cwd()

# Re-exported so existing imports (`from minimise.interfaces.cli import get_db`, etc.) work.
from minimise.interfaces.cli._shared import (  # noqa: E402  (constants must precede this)
    console,
    get_db,
    get_job_controller,
    resolve_job_id,
    resolve_loop_id,
    _error_job_not_found,
    _format_datetime,
    _filter_tasks_by_id,
    _get_and_validate_job,
)
from minimise.interfaces.cli.job import job  # noqa: E402
from minimise.interfaces.cli.loop import loop  # noqa: E402
from minimise.interfaces.cli.persona import persona  # noqa: E402
from minimise.interfaces.cli.view import view  # noqa: E402


@click.group()
def mini():
    """Minimise: plan orchestrator for multi-agent execution"""
    pass


mini.add_command(job)
mini.add_command(loop)
mini.add_command(persona)
mini.add_command(view)


def main():
    """Entry point for the CLI."""
    mini()


if __name__ == "__main__":
    main()
