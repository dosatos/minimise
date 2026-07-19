from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class Persona(BaseModel):
    name: str
    model: Optional[str] = None
    harness: Optional[str] = None
    system_prompt: str


def _version_key(stem: str) -> tuple:
    """Natural sort key for 'vN' stems; non-vN sort before any version."""
    return (1, int(stem[1:])) if stem[1:].isdigit() else (0, stem)


def load_builtin_personas() -> dict[str, Persona]:
    """Load versioned built-in personas shipped as package data.

    Walks builtin_personas/<category>/<persona>/*.md relative to this module.
    Registers f"mini:{category}:{persona}@{stem}" per version file plus a bare
    f"mini:{category}:{persona}" latest alias (following a `latest` symlink if
    present, else the highest vN — the symlink may not survive packaging).
    """
    root = Path(__file__).parent / "builtin_personas"
    personas: dict[str, Persona] = {}
    if not root.is_dir():
        return personas

    for persona_dir in sorted(p for p in root.glob("*/*") if p.is_dir()):
        category, persona = persona_dir.parent.name, persona_dir.name
        versions = sorted(
            (f for f in persona_dir.glob("*.md") if f.stem != "latest"),
            key=lambda f: _version_key(f.stem),
        )
        if not versions:
            continue
        for f in versions:
            key = f"mini:{category}:{persona}@{f.stem}"
            personas[key] = Persona(name=key, system_prompt=f.read_text())

        latest_link = persona_dir / "latest"
        latest = latest_link if latest_link.exists() else versions[-1]
        alias = f"mini:{category}:{persona}"
        personas[alias] = Persona(name=alias, system_prompt=latest.read_text())
    return personas


def load_personas(config_dir: Path) -> dict[str, Persona]:
    """Load built-in personas, then merge user personas.yaml from config_dir.

    Missing personas.yaml -> built-ins only. User keys starting with 'mini:'
    are rejected (reserved namespace).
    """
    personas = load_builtin_personas()
    path = config_dir / "personas.yaml"
    if not path.exists():
        return personas
    raw = yaml.safe_load(path.read_text()) or {}

    for name, spec in raw.items():
        if name.startswith("mini:"):
            raise ValueError(f"persona '{name}': 'mini:' is reserved for built-ins")
        prompt = spec.get("prompt")
        prompt_file = spec.get("prompt_file")
        model = spec.get("model")
        harness = spec.get("harness")
        if (prompt is None) == (prompt_file is None):
            raise ValueError(f"persona '{name}': set exactly one of prompt / prompt_file")
        if prompt_file is not None:
            fp = Path(prompt_file)
            if not fp.is_absolute():
                fp = config_dir / fp
            if not fp.is_file():
                raise ValueError(f"persona '{name}': prompt_file not found: {fp}")
            system_prompt = fp.read_text()
        else:
            system_prompt = prompt
        personas[name] = Persona(name=name, model=model, harness=harness, system_prompt=system_prompt)
    return personas
