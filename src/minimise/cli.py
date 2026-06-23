"""CLI interface for Minimise - plan orchestrator for multi-agent execution."""

import click
import json
import sqlite3
import signal
import os
import yaml
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
import pydantic

from minimise.models import JobStatus, TaskStatus, Plan
from minimise.terminal_ui import get_status_color, render_task_table_with_gantt
from minimise.plan_reviewer import PlanReviewer


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
@click.option("--skip-review", is_flag=True, help="Skip plan review (for trusted plans)")
def job_new(plan: str, skip_review: bool):
    """Create a new job from a plan file (does not execute)."""
    try:
        plan_path = Path(plan).resolve()

        if not plan_path.exists():
            console.print(f"[red]Error: Plan file not found at {plan_path}[/red]")
            raise SystemExit(1)

        # 1. Load and validate plan syntax
        try:
            plan_obj = Plan.from_yaml(plan_path)
        except pydantic.ValidationError as e:
            console.print("[red]Syntax validation failed:[/red]")
            for i, err in enumerate(e.errors(), 1):
                loc = ".".join(str(p) for p in err["loc"])
                console.print(f"  {i}. {loc}: {err['msg']}")
            raise SystemExit(1)

        console.print("[green]✓[/green] Plan syntax valid")

        # 2. Run agent-based review (unless skipped)
        if not skip_review:
            reviewer = PlanReviewer()
            console.print("[dim]🤖 Reviewing plan quality...[/dim]")
            findings = reviewer.review(plan_obj)

            if findings:
                console.print(f"\n[red]📋 Plan review failed ({len(findings)} issue(s) to address):[/red]")
                for i, finding in enumerate(findings, 1):
                    severity_color = {
                        "high": "red",
                        "medium": "yellow",
                        "low": "blue"
                    }.get(finding.severity, "white")

                    console.print(f"\n  {i}. [{severity_color}]{finding.title}[/{severity_color}]")
                    console.print(f"     Task: {finding.task_id}")
                    console.print(f"     {finding.description}")
                    if finding.suggestion:
                        console.print(f"     [dim]Suggestion: {finding.suggestion}[/dim]")

                console.print(
                    "\n[yellow]Address the findings above in the plan and re-run "
                    "`mini job new`.[/yellow]"
                )
                console.print(
                    "[dim]To bypass review for a trusted plan, re-run with --skip-review.[/dim]"
                )
                raise SystemExit(1)

            else:
                console.print("[green]✓[/green] Plan review passed")

        # 3. Create the job
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = job_manager.create_job(plan_path)

        if job_obj is None:
            console.print("[red]Error: Failed to create job[/red]")
            raise SystemExit(1)

        console.print(f"[green]✓ Job created[/green]")
        console.print(f"[bold]Job ID:[/bold] {job_obj.id}")
        console.print(f"[bold]Name:[/bold] {job_obj.name}")
        console.print(f"[bold]Status:[/bold] {job_obj.status.value}")
        console.print(f"[bold]Tasks:[/bold] {len(job_obj.tasks)}\n")
        console.print(f"[dim]Start with: mini job start {job_obj.id[:8]}[/dim]")

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="start")
@click.argument("job_id")
def job_start(job_id: str):
    """Start a job (spawns subprocess in background)."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = db.get_job(job_id)
        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        if job_obj.status != JobStatus.PENDING:
            console.print(f"[red]Error: Job must be in PENDING state to start (current: {job_obj.status.value})[/red]")
            raise SystemExit(1)

        pid = job_manager.start_job(job_id)

        if pid is None:
            console.print(f"[red]Error: Failed to start job[/red]")
            raise SystemExit(1)

        console.print(f"[green]Job started successfully[/green]")
        console.print(f"[bold]Job ID:[/bold] {job_id}")
        console.print(f"[bold]PID:[/bold] {pid}")
        console.print(f"[dim]Check status with: mini job status {job_id[:8]}[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="list")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
@click.option("--limit", type=int, default=10, help="Maximum number of jobs to display (default: 10)")
def job_list(format, limit):
    """List all jobs."""
    try:
        db = get_db()
        jobs = db.list_jobs(limit=limit)

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
                        "estimated_duration_min": sum(t.estimated_duration_min for t in tasks),
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
            table.add_column("Duration (min)", style="blue")

            for j in jobs:
                status_text = Text(j.status.value, style=get_status_color(j.status))
                tasks = db.list_tasks_for_job(j.id)
                task_count = len(tasks)
                completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
                created = j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "N/A"
                estimated_duration_min = sum(t.estimated_duration_min for t in tasks if t.estimated_duration_min)
                duration_text = str(estimated_duration_min) if estimated_duration_min > 0 else "-"

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
                    duration_text,
                )

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="status")
@click.argument("job_id")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def job_status(job_id: str, format: str):
    """Show job details and task progress."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = job_manager.get_job_status(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        # Job-level estimated-duration total, shared by both output formats.
        est_total = sum(t.estimated_duration_min for t in job_obj.tasks)

        if format == "json":
            # Build JSON output with task metadata
            tasks_data = []
            now = datetime.utcnow()
            for task in job_obj.tasks:
                task_data = {
                    "id": task.id,
                    "name": task.name,
                    "status": task.status.value,
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                }
                # Add duration_seconds if both start and end times exist
                if task.started_at and task.completed_at:
                    duration = (task.completed_at - task.started_at).total_seconds()
                    task_data["duration_seconds"] = duration
                # Add estimated_duration_min if present
                if task.estimated_duration_min is not None:
                    task_data["estimated_duration_min"] = task.estimated_duration_min
                    # Add remaining_seconds for running tasks
                    if task.status.value not in ["completed", "failed", "stopped"] and task.started_at:
                        elapsed_seconds = (now - task.started_at).total_seconds()
                        total_seconds = task.estimated_duration_min * 60
                        remaining_seconds = max(0, total_seconds - elapsed_seconds)
                        task_data["remaining_seconds"] = remaining_seconds
                tasks_data.append(task_data)

            output = {
                "id": job_obj.id,
                "name": job_obj.name,
                "status": job_obj.status.value,
                "created_at": job_obj.created_at.isoformat() if job_obj.created_at else None,
                "started_at": job_obj.started_at.isoformat() if job_obj.started_at else None,
                "completed_at": job_obj.completed_at.isoformat() if job_obj.completed_at else None,
                "tasks": tasks_data,
                "tasks_summary": {
                    "total": len(job_obj.tasks),
                    "completed": sum(1 for t in job_obj.tasks if t.status == TaskStatus.COMPLETED),
                    "estimated_duration_min": est_total,
                },
            }
            console.print(json.dumps(output, indent=2))
        else:
            # Display job details (table format)
            console.print(f"\n[bold]Job Details[/bold]")
            console.print(f"[bold]ID:[/bold] {job_obj.id}")
            console.print(f"[bold]Name:[/bold] {job_obj.name}")
            console.print(f"[bold]Status:[/bold] {job_obj.status.value}")
            console.print(f"[bold]Estimated Duration:[/bold] {est_total} min")
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
                table = render_task_table_with_gantt(job_obj, job_obj.tasks, now=datetime.utcnow())
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
    """Stop a running job (sends SIGTERM to subprocess)."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = db.get_job(job_id)
        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        if job_obj.status != JobStatus.RUNNING:
            console.print(f"[red]Error: Job must be in RUNNING state to stop (current: {job_obj.status.value})[/red]")
            raise SystemExit(1)

        if job_obj.pid is None:
            console.print(f"[red]Error: Job has no associated process[/red]")
            raise SystemExit(1)

        success = job_manager.stop_job(job_id)

        if success:
            console.print(f"[green]Job {job_id} stopped successfully[/green]")
        else:
            console.print(f"[red]Error: Failed to stop job {job_id}[/red]")
            raise SystemExit(1)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="delete")
@click.argument("job_id")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def job_delete(job_id: str, force: bool):
    """Delete a job and all its tasks (safeguards: only PENDING/COMPLETED/FAILED)."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()

        job_obj = db.get_job(job_id)
        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        if job_obj.status == JobStatus.RUNNING:
            console.print(f"[red]Error: Cannot delete RUNNING job. Stop it first with: mini job stop {job_id[:8]}[/red]")
            raise SystemExit(1)

        if job_obj.status == JobStatus.STOPPED:
            console.print(f"[red]Error: Cannot delete STOPPED job. Resume or mark as FAILED first.[/red]")
            raise SystemExit(1)

        tasks = db.list_tasks_for_job(job_id)
        task_count = len(tasks)

        console.print(f"[yellow]Delete job: {job_obj.name} (Status: {job_obj.status.value})[/yellow]")
        console.print(f"[yellow]This will remove {task_count} task(s)[/yellow]")

        if task_count > 0:
            console.print(f"[dim]Tasks:[/dim]")
            for task in tasks:
                console.print(f"  - {task.name} ({task.status.value})")

        if not force:
            if not click.confirm("Are you sure you want to delete this job?"):
                console.print("[yellow]Cancelled[/yellow]")
                return

        success = db.delete_job(job_id)

        if success:
            console.print(f"[green]Job {job_id} deleted successfully[/green]")
        else:
            console.print(f"[red]Error: Failed to delete job[/red]")
            raise SystemExit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="resume")
@click.argument("job_id")
def job_resume(job_id: str):
    """Resume a stopped or failed job from checkpoint."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = db.get_job(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        if job_obj.status not in [JobStatus.FAILED, JobStatus.STOPPED]:
            console.print(
                f"[yellow]Job must be in FAILED or STOPPED state. Current status: {job_obj.status.value}[/yellow]"
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


@job.group(name="results")
def job_results():
    """Retrieve job results (logs and diffs)"""
    pass


@job_results.command(name="logs")
@click.argument("job_id")
@click.option("--task-id", default=None, help="Filter by specific task ID")
def job_results_logs(job_id: str, task_id: Optional[str]):
    """Retrieve task output logs for a job."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()

        job_obj = db.get_job(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        tasks = db.list_tasks_for_job(job_id)

        if not tasks:
            console.print("[yellow]No tasks for this job[/yellow]")
            return

        # Filter by task_id if provided
        if task_id:
            tasks = [t for t in tasks if t.id == task_id or t.id.startswith(task_id)]
            if not tasks:
                console.print(f"[red]Error: Task '{task_id}' not found[/red]")
                raise SystemExit(1)

        console.print(f"\n[bold]Results Logs[/bold]")
        console.print(f"[bold]Job:[/bold] {job_obj.name} ({job_id})\n")

        for task in tasks:
            # Task header
            status_color = "green" if task.status == TaskStatus.COMPLETED else "yellow" if task.status == TaskStatus.RUNNING else "red" if task.status == TaskStatus.FAILED else "cyan"
            console.print(f"[bold {status_color}]{task.name}[/bold {status_color}]")
            console.print(f"  [dim]ID:[/dim] {task.id}")
            console.print(f"  [dim]Status:[/dim] {task.status.value}")
            console.print(f"  [dim]Created:[/dim] {task.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

            if task.started_at:
                console.print(f"  [dim]Started:[/dim] {task.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

            if task.completed_at:
                console.print(f"  [dim]Completed:[/dim] {task.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")

            console.print(f"  [dim]Retries:[/dim] {task.retries}")

            # Output
            if task.output:
                console.print(f"\n  [bold]Output:[/bold]")
                for line in task.output.split("\n"):
                    if line:
                        console.print(f"    {line}")
            else:
                console.print(f"\n  [dim]Output: (none)[/dim]")

            console.print()

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job_results.command(name="diff")
@click.argument("job_id")
@click.option("--task-id", default=None, help="Filter by specific task ID")
def job_results_diff(job_id: str, task_id: Optional[str]):
    """Retrieve git diffs for tasks in a job."""
    try:
        job_id = resolve_job_id(job_id)
        db = get_db()

        job_obj = db.get_job(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        tasks = db.list_tasks_for_job(job_id)

        if not tasks:
            console.print("[yellow]No tasks for this job[/yellow]")
            return

        # Filter by task_id if provided
        if task_id:
            tasks = [t for t in tasks if t.id == task_id or t.id.startswith(task_id)]
            if not tasks:
                console.print(f"[red]Error: Task '{task_id}' not found[/red]")
                raise SystemExit(1)

        console.print(f"\n[bold]Results Diffs[/bold]")
        console.print(f"[bold]Job:[/bold] {job_obj.name} ({job_id})\n")

        has_diffs = False

        for task in tasks:
            if task.diff_path:
                has_diffs = True
                # Task header
                status_color = "green" if task.status == TaskStatus.COMPLETED else "yellow" if task.status == TaskStatus.RUNNING else "red" if task.status == TaskStatus.FAILED else "cyan"
                console.print(f"[bold {status_color}]{task.name}[/bold {status_color}]")
                console.print(f"  [dim]ID:[/dim] {task.id}")
                console.print(f"  [dim]Diff Path:[/dim] {task.diff_path}")

                # Try to read and display the diff
                diff_file = Path(task.diff_path)
                if diff_file.exists():
                    console.print(f"\n  [bold]Diff Content:[/bold]")
                    try:
                        with open(diff_file, 'r') as f:
                            content = f.read()
                            for line in content.split("\n"):
                                if line:
                                    if line.startswith("+++") or line.startswith("---"):
                                        console.print(f"    [cyan]{line}[/cyan]")
                                    elif line.startswith("+"):
                                        console.print(f"    [green]{line}[/green]")
                                    elif line.startswith("-"):
                                        console.print(f"    [red]{line}[/red]")
                                    else:
                                        console.print(f"    {line}")
                    except Exception as e:
                        console.print(f"    [yellow]Could not read diff: {str(e)}[/yellow]")
                else:
                    console.print(f"    [yellow]Diff file not found at {task.diff_path}[/yellow]")

                console.print()
            else:
                # Show tasks without diffs
                console.print(f"[dim]{task.name}[/dim]")
                console.print(f"  [dim]ID:[/dim] {task.id}")
                console.print(f"  [dim]Status:[/dim] No diff available")
                console.print()

        if not has_diffs:
            console.print("[yellow]No diffs found for this job[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@job.command(name="show")
@click.argument("job_id")
@click.option("--task-id", default=None, help="Show full prompt with handover context for a specific task")
def job_show(job_id: str, task_id: Optional[str]):
    """Show job plan structure or full prompt for a specific task."""
    try:
        from minimise.handover_manager import HandoverManager
        import yaml

        job_id = resolve_job_id(job_id)
        db = get_db()
        job_manager = get_job_manager(db)

        job_obj = db.get_job(job_id)

        if job_obj is None:
            console.print(f"[red]Error: Job {job_id} not found[/red]")
            raise SystemExit(1)

        # If task_id is provided, show full prompt with handover context
        if task_id:
            tasks = db.list_tasks_for_job(job_id)
            matching_tasks = [t for t in tasks if t.id == task_id or t.id.startswith(task_id)]

            if not matching_tasks:
                console.print(f"[red]Error: Task '{task_id}' not found[/red]")
                raise SystemExit(1)

            if len(matching_tasks) > 1:
                console.print(f"[red]Error: Multiple tasks match '{task_id}':[/red]")
                for t in matching_tasks:
                    console.print(f"  {t.id}")
                console.print("[yellow]Please provide more characters to disambiguate[/yellow]")
                raise SystemExit(1)

            task = matching_tasks[0]

            console.print(f"\n[bold]Full Prompt for Task[/bold]")
            console.print(f"[bold]Job:[/bold] {job_obj.name} ({job_id})")
            console.print(f"[bold]Task:[/bold] {task.name} ({task.id})")
            console.print(f"[bold]Status:[/bold] {task.status.value}\n")

            # Display task description
            console.print(f"[bold cyan]Task Description[/bold cyan]")
            console.print(task.description)
            console.print()

            # If task is not the first one, show handover context
            all_tasks = db.list_tasks_for_job(job_id)
            task_index = next((i for i, t in enumerate(all_tasks) if t.id == task.id), None)

            if task_index and task_index > 0:
                previous_task = all_tasks[task_index - 1]
                console.print(f"[bold cyan]Handover Context[/bold cyan]")
                console.print(f"[dim]From previous task: {previous_task.name}[/dim]\n")

                # Show previous task output
                if previous_task.output:
                    console.print(f"[bold]Previous Task Output:[/bold]")
                    for line in previous_task.output.split("\n"):
                        if line:
                            console.print(f"  {line}")
                    console.print()

                # Show git diff since job start
                if job_obj.base_commit:
                    diff = job_manager.git_tracker.get_diff(job_obj.base_commit)
                    if diff:
                        console.print(f"[bold]Git Changes Summary:[/bold]")
                        file_count = diff.count("diff --git")
                        lines_added = len([l for l in diff.split("\n") if l.startswith("+") and not l.startswith("+++")])
                        lines_removed = len([l for l in diff.split("\n") if l.startswith("-") and not l.startswith("---")])
                        console.print(f"  Files changed: {file_count}")
                        console.print(f"  Lines added: {lines_added}")
                        console.print(f"  Lines removed: {lines_removed}\n")

                        # Show truncated diff
                        console.print(f"[bold]Diff Preview (first 2000 chars):[/bold]")
                        truncated_diff = diff[:2000]
                        if len(diff) > 2000:
                            truncated_diff += "\n..."
                        for line in truncated_diff.split("\n"):
                            if line:
                                if line.startswith("+++") or line.startswith("---"):
                                    console.print(f"  [cyan]{line}[/cyan]")
                                elif line.startswith("+"):
                                    console.print(f"  [green]{line}[/green]")
                                elif line.startswith("-"):
                                    console.print(f"  [red]{line}[/red]")
                                else:
                                    console.print(f"  {line}")
        else:
            # Show plan structure
            # Try cached plan first, fall back to original path for backward compat
            cached_plan_path = JOBS_DIR / job_id / "plan.yaml"
            original_plan_path = Path(job_obj.plan_path)

            plan_path = cached_plan_path if cached_plan_path.exists() else original_plan_path

            if not plan_path.exists():
                console.print(f"[red]Error: Plan file not found at {plan_path}[/red]")
                raise SystemExit(1)

            with open(plan_path, 'r') as f:
                plan_data = yaml.safe_load(f)

            # Handle both nested (plan.xxx) and flat (xxx) formats
            plan = plan_data.get('plan', plan_data) if isinstance(plan_data, dict) else plan_data

            console.print(f"\n[bold]Plan Structure[/bold]")
            console.print(f"[bold]Job:[/bold] {job_obj.name} ({job_id})")
            console.print(f"[bold]Plan Path:[/bold] {plan_path}")
            console.print(f"[bold]Status:[/bold] {job_obj.status.value}\n")

            # Display plan metadata
            if 'name' in plan:
                console.print(f"[bold]Plan Name:[/bold] {plan['name']}")
            if 'briefing' in plan:
                console.print(f"[bold]Briefing:[/bold] {plan['briefing']}")
            if 'documentation' in plan:
                console.print(f"[bold]Documentation:[/bold]")
                for line in plan['documentation'].strip().split("\n"):
                    console.print(f"  {line}")

            # Display tasks
            tasks = plan.get('tasks', [])
            console.print(f"\n[bold]Tasks ({len(tasks)})[/bold]")

            db_tasks = db.list_tasks_for_job(job_id)

            for i, task_plan in enumerate(tasks, 1):
                task_id = task_plan.get('id', f'task-{i}')
                task_name = task_plan.get('name', 'Unnamed')

                # Find corresponding db task to get status
                db_task = next((t for t in db_tasks if t.id == task_id), None)
                status = db_task.status.value if db_task else "not started"
                status_color = "green" if status == "completed" else "yellow" if status == "running" else "red" if status == "failed" else "cyan"

                console.print(f"\n  [{i}] [bold {status_color}]{task_name}[/bold {status_color}]")
                console.print(f"      [dim]ID:[/dim] {task_id}")
                console.print(f"      [dim]Status:[/dim] {status}")

                # Display goal if present
                if task_plan.get('goal'):
                    console.print(f"      [dim]Goal:[/dim] {task_plan['goal'][:70]}")

                # Display estimated_duration_min if present
                if task_plan.get('estimated_duration_min') is not None:
                    duration = task_plan['estimated_duration_min']
                    console.print(f"      [dim]Estimated Duration:[/dim] {duration} min")

                description = task_plan.get('description', 'No description')
                description_lines = description.strip().split("\n")
                for line in description_lines[:3]:
                    if line.strip():
                        console.print(f"      {line[:70]}")
                if len(description_lines) > 3:
                    console.print(f"      [dim]...[/dim]")

            console.print(f"\n[dim]View full prompt with: mini job show {job_id[:8]} --task-id <task-id>[/dim]")

    except SystemExit:
        raise
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
