"""`mini loop` subgroup: create, run, inspect, and manage refinement loops.

Mirrors cli/job.py. State/timing live in the DB (loops table); loop *content*
lives in journal.jsonl and narration in job.log — hence the extra `journal` verb.
"""

import json
from pathlib import Path
from typing import Optional

import click
import pydantic
from rich.table import Table
from rich.text import Text

import minimise.interfaces.cli as _cli  # patchable constants; read at call time
from minimise.models import JobStatus, LoopSpec
from minimise.interfaces.terminal_ui import (
    get_status_color,
    render_loop_progress_table,
    render_loop_stage_timing,
    loop_progress_summary,
    loop_stage_breadcrumb,
    format_duration,
)
from minimise.orchestration import loop_journal
from minimise.interfaces.cli._shared import (
    console,
    get_db,
    resolve_loop_id,
    _format_datetime,
)
from minimise.storage.loop_store import LoopStore
from minimise.orchestration.loop_engine import LoopEngine
from minimise.personas import load_personas


def _get_store(db) -> LoopStore:
    return LoopStore(db, _cli.JOBS_DIR)


def _spec_workers(spec: LoopSpec):
    """Every Worker in a spec: plan, implement, and each evaluate dimension."""
    return [spec.loop.plan, spec.loop.implement, *spec.loop.evaluate.dimensions]


@click.group(name="loop")
def loop():
    """Manage refinement loops"""
    pass


@loop.command(name="new")
@click.option("--plan", required=True, help="Path to loop spec YAML file")
def loop_new(plan: str):
    """Register a loop from a spec file (does not execute)."""
    try:
        plan_path = Path(plan).resolve()

        if not plan_path.exists():
            console.print(f"[red]Error: Spec file not found at {plan_path}[/red]")
            raise SystemExit(1)

        # 1. Load and validate spec syntax
        try:
            spec = LoopSpec.from_yaml(plan_path)
        except pydantic.ValidationError as e:
            console.print("[red]Syntax validation failed:[/red]")
            for i, err in enumerate(e.errors(), 1):
                loc = ".".join(str(p) for p in err["loc"])
                console.print(f"  {i}. {loc}: {err['msg']}")
            raise SystemExit(1)

        console.print("[green]✓[/green] Spec syntax valid")

        # 2. Resolve every worker persona against the registry
        try:
            personas = load_personas(_cli.CONFIG_DIR)
        except ValueError as e:
            console.print(f"[red]Persona config error: {e}[/red]")
            raise SystemExit(1)
        unknown = sorted({w.persona for w in _spec_workers(spec)
                          if w.persona and w.persona not in personas})
        if unknown:
            console.print(f"[red]Unknown persona(s): {', '.join(unknown)}[/red]")
            raise SystemExit(1)

        # 3. Register the loop
        db = get_db()
        loop_obj = _get_store(db).create(spec, str(plan_path))

        console.print(f"[green]✓ Loop created[/green]")
        console.print(f"[bold]Loop ID:[/bold] {loop_obj.loop_id}")
        console.print(f"[bold]Name:[/bold] {loop_obj.name}")
        console.print(f"[bold]Status:[/bold] {loop_obj.status.value}")
        console.print(f"[bold]Max Iterations:[/bold] {loop_obj.max_iterations}\n")
        console.print(f"[dim]Start with: mini loop start {loop_obj.loop_id[:8]}[/dim]")

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@loop.command(name="start")
@click.argument("loop_id")
def loop_start(loop_id: str):
    """Start or resume a loop in the foreground (idempotent).

    The engine sets status RUNNING + pid, resumes from the journal anchor, and
    runs to a terminal status. A live RUNNING loop is left alone; a COMPLETED
    loop is a no-op.
    """
    try:
        loop_id = resolve_loop_id(loop_id)
        db = get_db()
        store = _get_store(db)
        loop_obj = store.load(loop_id)

        if loop_obj.status == JobStatus.RUNNING:
            console.print(f"[yellow]Loop already running (pid {loop_obj.pid})[/yellow]")
            return
        if loop_obj.status == JobStatus.COMPLETED:
            console.print(f"[green]Loop already complete[/green]")
            return

        personas = load_personas(_cli.CONFIG_DIR)
        engine = LoopEngine(store=store, db=db, personas=personas, cwd=str(_cli.REPO_PATH))
        status = engine.run(loop_id)

        if status == JobStatus.FAILED:
            console.print(f"[red]Error: Loop failed[/red]")
            raise SystemExit(1)
        if status == JobStatus.STOPPED:
            console.print(f"[yellow]Loop stopped[/yellow]")
            return

        console.print(f"[green]Loop completed successfully[/green]")
        console.print(f"[bold]Loop ID:[/bold] {loop_id}")
        console.print(f"[dim]Check status with: mini loop status {loop_id[:8]}[/dim]")

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@loop.command(name="list")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
@click.option("--limit", type=int, default=10, help="Maximum number of loops to display (default: 10)")
def loop_list(format, limit):
    """List all loops."""
    try:
        db = get_db()
        loops = db.list_loops(limit=limit)

        if not loops:
            if format == "json":
                console.print(json.dumps([], indent=2))
            else:
                console.print("[yellow]No loops found[/yellow]")
            return

        if format == "json":
            loops_data = [{
                "loop_id": lp.loop_id,
                "name": lp.name,
                "status": lp.status.value,
                "iteration": _iteration(db, lp),
                "max_iterations": lp.max_iterations,
                "created_at": lp.created_at.isoformat() if lp.created_at else None,
                "started_at": lp.started_at.isoformat() if lp.started_at else None,
                "completed_at": lp.completed_at.isoformat() if lp.completed_at else None,
            } for lp in loops]
            console.print(json.dumps(loops_data, indent=2))
        else:
            table = Table(title="Minimise Loops")
            table.add_column("Loop ID", style="cyan")
            table.add_column("Name", style="magenta")
            table.add_column("Status", style="cyan")
            table.add_column("Created", style="green")
            table.add_column("Iter", style="yellow")

            for lp in loops:
                status_text = Text(lp.status.value, style=get_status_color(lp.status))
                table.add_row(
                    lp.loop_id,
                    lp.name,
                    status_text,
                    _format_datetime(lp.created_at),
                    f"{_iteration(db, lp)}/{lp.max_iterations}",
                )

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


def _iteration(db, loop_obj) -> int:
    """Iter numerator = MAX(iteration) over the loop's steps (db.current_iteration)."""
    return db.current_iteration(loop_obj.loop_id)


@loop.command(name="status")
@click.argument("loop_id")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def loop_status(loop_id: str, format: str):
    """Show loop details and iteration progress."""
    try:
        loop_id = resolve_loop_id(loop_id)
        db = get_db()
        loop_obj = _get_store(db).load(loop_id)

        if loop_obj is None:
            console.print(f"[red]Error: Loop {loop_id} not found[/red]")
            raise SystemExit(1)

        iteration = _iteration(db, loop_obj)

        if format == "json":
            output = {
                "loop_id": loop_obj.loop_id,
                "name": loop_obj.name,
                "status": loop_obj.status.value,
                "iteration": iteration,
                "max_iterations": loop_obj.max_iterations,
                "plan_path": loop_obj.plan_path,
                "created_at": loop_obj.created_at.isoformat() if loop_obj.created_at else None,
                "started_at": loop_obj.started_at.isoformat() if loop_obj.started_at else None,
                "completed_at": loop_obj.completed_at.isoformat() if loop_obj.completed_at else None,
            }
            console.print(json.dumps(output, indent=2))
        else:
            console.print(f"\n[bold]Loop Details[/bold]")
            console.print(f"[bold]ID:[/bold] {loop_obj.loop_id}")
            console.print(f"[bold]Name:[/bold] {loop_obj.name}")
            console.print(f"[bold]Status:[/bold] {loop_obj.status.value}")
            console.print(f"[bold]Iteration:[/bold] {iteration}/{loop_obj.max_iterations}")
            console.print(f"[bold]Spec Path:[/bold] {loop_obj.plan_path}")
            console.print(f"[bold]Created:[/bold] {_format_datetime(loop_obj.created_at)}")
            if loop_obj.started_at:
                console.print(f"[bold]Started:[/bold] {_format_datetime(loop_obj.started_at)}")
            if loop_obj.completed_at:
                console.print(f"[bold]Completed:[/bold] {_format_datetime(loop_obj.completed_at)}")
            if loop_obj.started_at:
                elapsed = format_duration(
                    loop_obj.started_at, loop_obj.completed_at,
                    is_running=(loop_obj.status == JobStatus.RUNNING), now=None,
                )
                console.print(f"[bold]Elapsed:[/bold] {elapsed}")
            records = loop_journal.read(_get_store(db).journal_path(loop_id))
            # Spec dimension order seeds all rows upfront; tolerate a missing/bad spec.
            dims = None
            try:
                spec = LoopSpec.from_yaml(Path(loop_obj.plan_path))
                dims = [d.name for d in spec.loop.evaluate.dimensions]
            except Exception:
                pass
            steps = db.list_loop_steps(loop_id)
            if loop_obj.started_at:
                console.print("[bold]Stage:[/bold] ", end="")
                console.print(loop_stage_breadcrumb(loop_obj, steps))
                console.print()
                console.print("[bold]Stage timing[/bold]")
                console.print(render_loop_stage_timing(loop_obj, steps, dims))
                console.print()
                console.print("[bold]Verdicts[/bold]")
                console.print(render_loop_progress_table(loop_obj, records, dims, steps))
                summary = loop_progress_summary(records, dims)
                if summary:
                    console.print(f"[dim]{summary}[/dim]")
            console.print(f"[dim]View journal with: mini loop journal {loop_id[:8]}[/dim]")

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


@loop.command(name="stop")
@click.argument("loop_id")
def loop_stop(loop_id: str):
    """Stop a running loop (the engine polls loops.status)."""
    try:
        loop_id = resolve_loop_id(loop_id)
        db = get_db()
        loop_obj = _get_store(db).load(loop_id)

        if loop_obj.status != JobStatus.RUNNING:
            console.print(f"[red]Error: Loop must be in RUNNING state to stop (current: {loop_obj.status.value})[/red]")
            raise SystemExit(1)

        db.update_loop_status(loop_id, status=JobStatus.STOPPED)
        console.print(f"[green]Loop {loop_id} stopped[/green]")

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


def _render_record(rec: dict, fields: list) -> str:
    """Project a record to `fields`; `@message`/no fields → whole record JSON."""
    if not fields:
        return json.dumps(rec)
    parts = [json.dumps(rec) if f == "@message" else str(rec.get(f, "")) for f in fields]
    return "\t".join(parts)


@loop.command(name="logs")
@click.argument("loop_id")
@click.option("-f", "--follow", is_flag=True, help="Tail the log live (Ctrl-C to stop)")
@click.option("--query", default=None, help="CloudWatch Insights-style query (fields|filter|sort|limit)")
@click.option("--json", "as_json", is_flag=True, help="Emit raw matching JSONL records (for jq)")
def loop_logs(loop_id: str, follow: bool, query: Optional[str], as_json: bool):
    """View the agent narration log for a loop (live with -f)."""
    loop_id = resolve_loop_id(loop_id)
    db = get_db()
    log_path = _get_store(db).loop_log_path(loop_id)
    _emit_jsonl(loop_id, db, log_path, follow, query, as_json,
                empty_msg="No logs yet for this loop.")


@loop.command(name="journal")
@click.argument("loop_id")
@click.option("--iteration", type=int, default=None, help="Only records from iteration N")
@click.option("-f", "--follow", is_flag=True, help="Tail the journal live (Ctrl-C to stop)")
@click.option("--query", default=None, help="CloudWatch Insights-style query (fields|filter|sort|limit)")
@click.option("--json", "as_json", is_flag=True, help="Emit raw matching JSONL records (for jq)")
def loop_journal_cmd(loop_id: str, iteration: Optional[int], follow: bool,
                     query: Optional[str], as_json: bool):
    """View the loop's control journal (plan/implement/evaluate lines + commit markers).

    This is the loop's memory across iterations: each iteration's planner reads
    the journal so far before deciding the next step.

    ``--iteration N`` appends a ``filter iteration = "N"`` clause to whatever
    ``--query`` you pass (or forms the whole query on its own).
    """
    loop_id = resolve_loop_id(loop_id)
    db = get_db()
    journal_path = _get_store(db).journal_path(loop_id)
    if iteration is not None:
        clause = f'filter iteration = "{iteration}"'
        query = f"{query} | {clause}" if query else clause
    _emit_jsonl(loop_id, db, journal_path, follow, query, as_json,
                empty_msg="No journal entries yet for this loop.")


def _emit_jsonl(loop_id, db, path, follow, query, as_json, empty_msg):
    """Shared read/tail path for loop logs + journal (both plain JSONL)."""
    from minimise.logging.backend import JsonlLogBackend
    from minimise.logging.log_query import parse_query

    try:
        if not Path(path).exists():
            console.print(f"[yellow]{empty_msg}[/yellow]")
            return

        # No query → raw print/tail (byte-for-byte like job_logs).
        if query is None:
            with open(path, "r", encoding="utf-8") as f:
                console.out(f.read(), end="")
                if not follow:
                    return
                _tail(f, db, loop_id, None, None, as_json)
            return

        try:
            log_query = parse_query(query)
        except ValueError as e:
            console.print(f"[red]Error: {str(e)}[/red]")
            raise SystemExit(1)

        backend = JsonlLogBackend()
        for rec in backend.search(path, log_query):
            console.out(json.dumps(rec) if as_json else _render_record(rec, log_query.fields))

        if not follow:
            return
        if log_query.sort_present or log_query.limit is not None:
            click.echo("(live: sort/limit ignored; filter applied per line)", err=True)
        with open(path, "r", encoding="utf-8") as f:
            f.seek(0, 2)
            _tail(f, db, loop_id, backend, log_query, as_json)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


def _tail(f, db, loop_id, backend, log_query, as_json) -> None:
    """Poll for appended lines until the loop leaves RUNNING.

    backend/log_query None → raw echo; otherwise render through the filter.
    """
    import time

    def emit(line: str) -> None:
        if backend is None:
            console.out(line, end="")
            return
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
        console.out(json.dumps(rec) if as_json else _render_record(rec, log_query.fields))

    try:
        while True:
            line = f.readline()
            if line:
                emit(line)
                continue
            fresh = db.get_loop(loop_id)
            if fresh is None or fresh.status != JobStatus.RUNNING:
                rest = f.read()
                for chunk in (rest.splitlines() if backend else [rest]):
                    emit(chunk)
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
