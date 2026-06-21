"""CLI interface for Minimise - plan orchestrator for multi-agent execution."""

import click
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional
from rich.table import Table
from rich.console import Console
from rich.text import Text

from minimise.database import Database
from minimise.job_manager import JobManager
from minimise.git_tracker import GitTracker
from minimise.api_server import APIServer
from minimise.models import JobStatus, TaskStatus
from minimise.terminal_ui import get_status_color, render_task_table_with_gantt


# Global constants
CONFIG_DIR = Path.home() / ".minimise"
DB_PATH = CONFIG_DIR / "minimise.db"
JOBS_DIR = CONFIG_DIR / "jobs"
REPO_PATH = Path.cwd()


console = Console()


def get_db() -> Database:
    """Get or create database instance."""
    db = Database(DB_PATH)
    db.init_db()
    return db


def get_job_manager(db: Database) -> JobManager:
    """Get job manager instance."""
    git_tracker = GitTracker(REPO_PATH)
    return JobManager(db, git_tracker, JOBS_DIR, REPO_PATH)


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




@click.group()
def mini():
    """Minimise: plan orchestrator for multi-agent execution"""
    pass


@mini.group(name="job")
def job():
    """Manage jobs"""
    pass


@job.command(name="new")
@click.option("--plan", required=True, help="Path to plan.yaml file")
def job_new(plan: str):
    """Create and execute a new job from a plan file."""
    try:
        plan_path = Path(plan).resolve()

        if not plan_path.exists():
            console.print(f"[red]Error: Plan file not found at {plan_path}[/red]")
            raise SystemExit(1)

        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = job_manager.create_job(plan_path)

        if job_obj is None:
            console.print("[red]Error: Failed to create job[/red]")
            raise SystemExit(1)

        console.print(f"[green]Job created[/green]")
        console.print(f"[bold]Job ID:[/bold] {job_obj.id}")
        console.print(f"[bold]Name:[/bold] {job_obj.name}")
        console.print(f"[bold]Tasks:[/bold] {len(job_obj.tasks)}\n")

        # Execute the job immediately
        console.print("[cyan]Executing job...[/cyan]")
        success = job_manager.run_job(job_obj.id)

        if success:
            console.print(f"[green]Job completed successfully[/green]")
        else:
            console.print(f"[red]Job failed[/red]")
            raise SystemExit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="list")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def job_list(format):
    """List all jobs."""
    try:
        db = get_db()
        jobs = db.list_jobs()

        if not jobs:
            if format == "json":
                console.print(json.dumps([], indent=2))
            else:
                console.print("[yellow]No jobs found[/yellow]")
            return

        if format == "json":
            jobs_data = []
            for j in jobs:
                tasks = db.list_tasks_for_job(j.id)
                task_count = len(tasks)
                completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)

                jobs_data.append({
                    "id": j.id,
                    "name": j.name,
                    "status": j.status.value,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                    "tasks": {
                        "total": task_count,
                        "completed": completed_count,
                    },
                })
            console.print(json.dumps(jobs_data, indent=2))
        else:
            table = Table(title="Minimise Jobs")
            table.add_column("Job ID", style="cyan")
            table.add_column("Name", style="magenta")
            table.add_column("Status", style="cyan")
            table.add_column("Created", style="green")
            table.add_column("Progress", style="yellow")

            for j in jobs:
                status_text = Text(j.status.value, style=get_status_color(j.status))
                tasks = db.list_tasks_for_job(j.id)
                task_count = len(tasks)
                completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
                created = j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "N/A"

                progress_text = f"{completed_count}/{task_count}"
                if completed_count == task_count and task_count > 0:
                    progress = Text(progress_text, style="green")
                elif completed_count > 0:
                    progress = Text(progress_text, style="yellow")
                else:
                    progress = Text(progress_text, style="red")

                table.add_row(
                    j.id,
                    j.name,
                    status_text,
                    created,
                    progress,
                )

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="status")
@click.argument("job_id")
def job_status(job_id: str):
    """Show job details and task progress."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = job_manager.get_job_status(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        # Display job details
        console.print(f"\n[bold]Job Details[/bold]")
        console.print(f"[bold]ID:[/bold] {job_obj.id}")
        console.print(f"[bold]Name:[/bold] {job_obj.name}")
        console.print(f"[bold]Status:[/bold] {job_obj.status.value}")
        console.print(f"[bold]Plan Path:[/bold] {job_obj.plan_path}")
        console.print(f"[bold]Base Commit:[/bold] {job_obj.base_commit or 'N/A'}")
        console.print(
            f"[bold]Created:[/bold] {job_obj.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if job_obj.started_at:
            console.print(
                f"[bold]Started:[/bold] {job_obj.started_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        if job_obj.completed_at:
            console.print(
                f"[bold]Completed:[/bold] {job_obj.completed_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        # Display task progress with Gantt chart
        if job_obj.tasks:
            console.print(f"\n[bold]Task Progress[/bold]")
            table = render_task_table_with_gantt(job_obj, job_obj.tasks)
            console.print(table)
            console.print(f"\n[dim]View full output with: mini job logs {job_id[:8]}[/dim]")
        else:
            console.print("[yellow]No tasks for this job[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="stop")
@click.argument("job_id")
def job_stop(job_id: str):
    """Cancel a running job."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        success = job_manager.cancel_job(job_id)

        if success:
            console.print(f"[green]Job {job_id} cancelled successfully[/green]")
        else:
            console.print(f"[red]Error: Failed to cancel job {job_id}[/red]")
            raise SystemExit(1)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="delete")
@click.argument("job_id")
def job_delete(job_id: str):
    """Delete a job and all its tasks."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()

        success = db.delete_job(job_id)

        if success:
            console.print(f"[green]Job {job_id} deleted successfully[/green]")
        else:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="resume")
@click.argument("job_id")
def job_resume(job_id: str):
    """Retry failed job from checkpoint."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = db.get_job(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        if job_obj.status != JobStatus.FAILED:
            console.print(
                f"[yellow]Job is not in FAILED state. Current status: {job_obj.status.value}[/yellow]"
            )
            return

        # Re-run the job
        success = job_manager.run_job(job_id)

        if success:
            console.print(f"[green]Job {job_id} resumed and completed successfully[/green]")
        else:
            console.print(f"[red]Error: Job {job_id} failed during resume[/red]")
            raise SystemExit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="logs")
@click.argument("job_id")
def job_logs(job_id: str):
    """View job output and logs."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()

        job_obj = db.get_job(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        tasks = db.list_tasks_for_job(job_id)

        console.print(f"\n[bold]Job Logs for {job_obj.name}[/bold]")
        console.print(f"[bold]Job ID:[/bold] {job_id}\n")

        if not tasks:
            console.print("[yellow]No tasks for this job[/yellow]")
            return

        for task in tasks:
            console.print(f"[bold cyan]{task.name}[/bold cyan]")
            console.print(f"  Status: {task.status.value}")
            console.print(f"  Retries: {task.retries}")

            if task.output:
                console.print(f"  Output:")
                for line in task.output.split("\n"):
                    console.print(f"    {line}")
            else:
                console.print(f"  Output: (none)")

            if task.diff_path:
                console.print(f"  Diff: {task.diff_path}")

            console.print()

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@mini.group(name="view")
def view():
    """Manage web UI"""
    pass


@view.command(name="start")
@click.option(
    "--port",
    default=5000,
    help="Port to run the web server on (default: 5000)",
)
def view_start(port: int):
    """Launch web UI (and start server if not running)."""
    try:
        db = get_db()
        job_manager = get_job_manager(db)

        api_server = APIServer(db, job_manager, port=port)

        console.print(f"[green]Starting web server on port {port}...[/green]")
        api_server.start()

        console.print(f"[green]Web UI available at:[/green] http://localhost:{port}")
        console.print("[yellow]Press Ctrl+C to stop[/yellow]")

        # Keep the process running
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping web server...[/yellow]")
            api_server.stop()
            console.print("[green]Web server stopped[/green]")

    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@view.command(name="stop")
def view_stop():
    """Stop web server."""
    try:
        console.print("[yellow]Note: Server stop requires the running server process[/yellow]")
        console.print("[yellow]Press Ctrl+C in the server terminal or kill the process[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


def main():
    """Entry point for the CLI."""
    mini()


if __name__ == "__main__":
    main()
