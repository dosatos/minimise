"""`mini job` subgroup: create, run, inspect, and manage jobs."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import pydantic
from rich.table import Table
from rich.text import Text

import minimise.interfaces.cli as _cli  # patchable constants/PlanReviewer; read at call time
from minimise.models import JobStatus, TaskStatus, Plan
from minimise.interfaces.terminal_ui import get_status_color, render_execution_table_with_gantt, humanize_duration
from minimise.interfaces.cli._shared import (
    console,
    get_db,
    get_job_controller,
    resolve_job_id,
    _error_job_not_found,
    _format_datetime,
    _filter_tasks_by_id,
    _get_and_validate_job,
)
from minimise.interfaces.cli.results import job_results


@click.group(name="job")
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
            reviewer = _cli.PlanReviewer()
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

            console.print("[green]✓[/green] Plan review passed")

        # 3. Create the job
        db = get_db()
        job_controller = get_job_controller(db)

        job_obj = job_controller.create_job(plan_path)

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
    """Start a job (runs to completion in the foreground)."""
    try:
        job_id, db, job_obj = _get_and_validate_job(job_id)
        job_controller = get_job_controller(db)

        if job_obj.status != JobStatus.PENDING:
            console.print(f"[red]Error: Job must be in PENDING state to start (current: {job_obj.status.value})[/red]")
            raise SystemExit(1)

        success = job_controller.start_job(job_id)

        if not success:
            console.print(f"[red]Error: Job failed[/red]")
            raise SystemExit(1)

        console.print(f"[green]Job completed successfully[/green]")
        console.print(f"[bold]Job ID:[/bold] {job_id}")
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
                created = _format_datetime(j.created_at)
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
        job_controller = get_job_controller(db)

        job_obj = job_controller.get_job_status(job_id)

        if job_obj is None:
            _error_job_not_found(job_id)

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
            if job_obj.started_at and job_obj.completed_at:
                elapsed = (job_obj.completed_at - job_obj.started_at).total_seconds()
                console.print(f"[bold]Total Time:[/bold] {humanize_duration(elapsed)}")
            console.print(f"[bold]Plan Path:[/bold] {job_obj.plan_path}")
            console.print(f"[bold]Base Commit:[/bold] {job_obj.base_commit or 'N/A'}")
            console.print(
                f"[bold]Created:[/bold] {_format_datetime(job_obj.created_at)}"
            )
            if job_obj.started_at:
                console.print(
                    f"[bold]Started:[/bold] {_format_datetime(job_obj.started_at)}"
                )
            if job_obj.completed_at:
                console.print(
                    f"[bold]Completed:[/bold] {_format_datetime(job_obj.completed_at)}"
                )

            done = sum(1 for t in job_obj.tasks if t.status == TaskStatus.COMPLETED)
            console.print(f"[bold]Tasks Completed:[/bold] {done}/{len(job_obj.tasks)}")

            # Display task progress with Gantt chart
            if job_obj.tasks:
                console.print(f"\n[bold]Task Progress[/bold]")
                executions = db.list_executions_for_job(job_obj.id)
                try:
                    plan = job_controller.store.load_plan(job_obj.id)
                except Exception:
                    plan = None
                table = render_execution_table_with_gantt(
                    job_obj,
                    job_obj.tasks,
                    now=datetime.utcnow(),
                    executions=executions,
                    plan=plan,
                )
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
    """Stop a running job (marks it and its tasks STOPPED)."""
    try:
        job_id, db, job_obj = _get_and_validate_job(job_id)
        job_controller = get_job_controller(db)

        if job_obj.status != JobStatus.RUNNING:
            console.print(f"[red]Error: Job must be in RUNNING state to stop (current: {job_obj.status.value})[/red]")
            raise SystemExit(1)

        success = job_controller.stop_job(job_id)

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
def job_delete(job_id: str):
    """Delete a job and all its tasks (any status except RUNNING)."""
    try:
        job_id, db, job_obj = _get_and_validate_job(job_id)

        if job_obj.status == JobStatus.RUNNING:
            console.print(f"[red]Error: Cannot delete RUNNING job. Stop it first with: mini job stop {job_id[:8]}[/red]")
            raise SystemExit(1)

        task_count = len(db.list_tasks_for_job(job_id))

        if db.delete_job(job_id):
            console.print(f"[green]Deleted job {job_id} and {task_count} task(s)[/green]")
        else:
            console.print(f"[red]Error: Failed to delete job[/red]")
            raise SystemExit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


def _render_record(rec: dict, fields: list) -> str:
    """Project a record to `fields` (in order); `@message`/omit → whole record JSON."""
    if not fields:
        return json.dumps(rec)
    parts = []
    for f in fields:
        parts.append(json.dumps(rec) if f == "@message" else str(rec.get(f, "")))
    return "\t".join(parts)


@job.command(name="logs")
@click.argument("job_id")
@click.option("-f", "--follow", is_flag=True, help="Tail the log live (Ctrl-C to stop)")
@click.option("--query", default=None, help="CloudWatch Insights-style query (fields|filter|sort|limit)")
@click.option("--json", "as_json", is_flag=True, help="Emit raw matching JSONL records (for jq)")
def job_logs(job_id: str, follow: bool, query: Optional[str], as_json: bool):
    """View the agent narration log for a job (live with -f).

    Reads the per-job ``job.log`` written by the harness. Without ``--query`` the
    raw file is printed; ``--query`` parses an Insights-style string, runs it via
    the JSONL backend, and renders the projected ``fields``. The per-task
    status/duration timeline lives in ``mini job status``.
    """
    from minimise.logging.backend import JsonlLogBackend
    from minimise.logging.log_query import parse_query

    try:
        job_id, db, job_obj = _get_and_validate_job(job_id)
        log_path = _cli.JOBS_DIR / job_id / "job.log"

        if not log_path.exists():
            console.print("[yellow]No logs yet for this job.[/yellow]")
            return

        # No query → keep the original raw print/tail path byte-for-byte.
        if query is None:
            with open(log_path, "r", encoding="utf-8") as f:
                console.out(f.read(), end="")
                if not follow:
                    return
                _tail_raw(f, db, job_id)
            return

        try:
            log_query = parse_query(query)
        except ValueError as e:
            console.print(f"[red]Error: {str(e)}[/red]")
            raise SystemExit(1)

        backend = JsonlLogBackend()
        for rec in backend.search(log_path, log_query):
            console.out(json.dumps(rec) if as_json
                        else _render_record(rec, log_query.fields))

        if not follow:
            return

        # Live tail: only the filter applies per new line; sort/limit can't on a
        # stream, so flag that once and keep going.
        if log_query.sort_present or log_query.limit is not None:
            click.echo("(live: sort/limit ignored; filter applied per line)", err=True)
        with open(log_path, "r", encoding="utf-8") as f:
            f.seek(0, 2)  # past the already-rendered backlog
            _tail_filtered(f, db, job_id, backend, log_query, as_json)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


def _tail_raw(f, db, job_id: str) -> None:
    """Poll for appended lines, stop once the job leaves RUNNING."""
    import time
    try:
        while True:
            line = f.readline()
            if line:
                console.out(line, end="")
                continue
            fresh = db.get_job(job_id)
            if fresh is None or fresh.status != JobStatus.RUNNING:
                console.out(f.read(), end="")
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass


def _tail_filtered(f, db, job_id: str, backend, log_query, as_json: bool) -> None:
    """Tail like `_tail_raw` but render each new line through the query's filter."""
    import time

    def emit(line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                rec = {"message": line}
        except json.JSONDecodeError:
            rec = {"message": line}
        if not backend.matches(log_query, rec):
            return
        console.out(json.dumps(rec) if as_json
                    else _render_record(rec, log_query.fields))

    try:
        while True:
            line = f.readline()
            if line:
                emit(line)
                continue
            fresh = db.get_job(job_id)
            if fresh is None or fresh.status != JobStatus.RUNNING:
                for rest in f.read().splitlines():
                    emit(rest)
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass


@job.command(name="show")
@click.argument("job_id")
@click.option("--task-id", default=None, help="Show full prompt with handover context for a specific task")
def job_show(job_id: str, task_id: Optional[str]):
    """Show job plan structure or full prompt for a specific task."""
    try:
        from minimise.orchestration.handover_manager import HandoverManager
        import yaml

        job_id, db, job_obj = _get_and_validate_job(job_id)
        job_controller = get_job_controller(db)

        # If task_id is provided, show full prompt with handover context
        if task_id:
            tasks = db.list_tasks_for_job(job_id)
            matching_tasks = _filter_tasks_by_id(tasks, task_id)

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
                    diff = job_controller.git_tracker.get_diff(job_obj.base_commit)
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
            cached_plan_path = _cli.JOBS_DIR / job_id / "plan.yaml"
            original_plan_path = Path(job_obj.plan_path)

            plan_path = cached_plan_path if cached_plan_path.exists() else original_plan_path

            if not plan_path.exists():
                console.print(f"[red]Error: Plan file not found at {plan_path}[/red]")
                raise SystemExit(1)

            plan = Plan.from_yaml(plan_path)

            console.print(f"\n[bold]Plan Structure[/bold]")
            console.print(f"[bold]Job:[/bold] {job_obj.name} ({job_id})")
            console.print(f"[bold]Plan Path:[/bold] {plan_path}")
            console.print(f"[bold]Status:[/bold] {job_obj.status.value}\n")

            # Display plan metadata (briefing/documentation are pydantic extras)
            console.print(f"[bold]Plan Name:[/bold] {plan.name}")
            briefing = getattr(plan, 'briefing', None)
            if briefing:
                console.print(f"[bold]Briefing:[/bold] {briefing}")
            documentation = getattr(plan, 'documentation', None)
            if documentation:
                console.print(f"[bold]Documentation:[/bold]")
                for line in documentation.strip().split("\n"):
                    console.print(f"  {line}")

            # Display tasks
            console.print(f"\n[bold]Tasks ({len(plan.tasks)})[/bold]")

            db_tasks = db.list_tasks_for_job(job_id)

            for i, task_plan in enumerate(plan.tasks, 1):
                task_id = task_plan.id
                task_name = task_plan.name

                # Find corresponding db task to get status
                db_task = next((t for t in db_tasks if t.id == task_id), None)
                status = db_task.status.value if db_task else "not started"
                status_color = "green" if status == "completed" else "yellow" if status == "running" else "red" if status == "failed" else "cyan"

                console.print(f"\n  [{i}] [bold {status_color}]{task_name}[/bold {status_color}]")
                console.print(f"      [dim]ID:[/dim] {task_id}")
                console.print(f"      [dim]Status:[/dim] {status}")

                # Display goal if present
                if task_plan.goal:
                    console.print(f"      [dim]Goal:[/dim] {task_plan.goal[:70]}")

                # Display estimated_duration_min
                console.print(f"      [dim]Estimated Duration:[/dim] {task_plan.estimated_duration_min} min")

                description = task_plan.description or 'No description'
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


job.add_command(job_results)
