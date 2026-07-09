"""`mini persona` subgroup: discover built-in and user personas."""

import click

import minimise.interfaces.cli as _cli  # patchable CONFIG_DIR; read at call time
from minimise.interfaces.cli._shared import console
from minimise.personas import load_personas


def _summary(system_prompt: str, width: int = 70) -> str:
    """First non-empty line of the prompt, truncated to width."""
    line = next((ln.strip() for ln in system_prompt.splitlines() if ln.strip()), "")
    return line if len(line) <= width else line[: width - 1] + "…"


@click.group(name="persona")
def persona():
    """List and inspect personas"""
    pass


@persona.command(name="list")
def persona_list():
    """List every persona (built-ins first)."""
    personas = load_personas(_cli.CONFIG_DIR)
    # Bare `latest` names only — skip the per-version @vN aliases.
    names = [n for n in personas if "@" not in n]
    builtins = sorted(n for n in names if n.startswith("mini:"))
    users = sorted(n for n in names if not n.startswith("mini:"))
    for tag, group in (("BUILTIN", builtins), ("USER", users)):
        for name in group:
            console.print(f"{tag}  {name}  {_summary(personas[name].system_prompt)}")


@persona.command(name="show")
@click.argument("name")
def persona_show(name: str):
    """Print the full system prompt for NAME (bare or NAME@vN)."""
    personas = load_personas(_cli.CONFIG_DIR)
    p = personas.get(name)
    if p is None:
        console.print(f"[red]Error: unknown persona '{name}'[/red]")
        raise SystemExit(1)
    console.print(p.system_prompt)
