"""`mini job results` subgroup: retrieve task output logs and git diffs."""

from pathlib import Path
from typing import Optional

import click

from minimise.models import TaskStatus
from minimise.interfaces.cli._shared import (
    console,
    _get_and_validate_job,
    _format_datetime,
    _filter_tasks_by_id,
)


@click.group(name="results")
def job_results():
    """Retrieve job results (logs and diffs)"""
    pass


@job_results.command(name="logs")
@click.argument("job_id")
@click.option("--task-id", default=None, help="Filter by specific task ID")
def job_results_logs(job_id: str, task_id: Optional[str]):
    """Retrieve task output logs for a job."""
    try:
        job_id, db, job_obj = _get_and_validate_job(job_id)

        tasks = db.list_tasks_for_job(job_id)

        if not tasks:
            console.print("[yellow]No tasks for this job[/yellow]")
            return

        # Filter by task_id if provided
        if task_id:
            tasks = _filter_tasks_by_id(tasks, task_id)
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
            console.print(f"  [dim]Created:[/dim] {_format_datetime(task.created_at)}")

            if task.started_at:
                console.print(f"  [dim]Started:[/dim] {_format_datetime(task.started_at)}")

            if task.completed_at:
                console.print(f"  [dim]Completed:[/dim] {_format_datetime(task.completed_at)}")

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
        job_id, db, job_obj = _get_and_validate_job(job_id)

        tasks = db.list_tasks_for_job(job_id)

        if not tasks:
            console.print("[yellow]No tasks for this job[/yellow]")
            return

        # Filter by task_id if provided
        if task_id:
            tasks = _filter_tasks_by_id(tasks, task_id)
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
