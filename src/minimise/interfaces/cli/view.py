"""`mini view` subgroup: manage the web UI / API server."""

import time

import click

from minimise.interfaces.api_server import APIServer
from minimise.interfaces.cli._shared import console, get_db, get_job_controller


@click.group(name="view")
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
        job_controller = get_job_controller(db)

        api_server = APIServer(db, job_controller, port=port)

        console.print(f"[green]Starting web server on port {port}...[/green]")
        api_server.start()

        console.print(f"[green]Web UI available at:[/green] http://localhost:{port}")
        console.print("[yellow]Press Ctrl+C to stop[/yellow]")

        # Keep the process running
        try:
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
