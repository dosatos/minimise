from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class Persona(BaseModel):
    name: str
    model: Optional[str] = None
    system_prompt: str


def load_personas(config_dir: Path) -> dict[str, Persona]:
    """Load personas.yaml from config_dir. Missing file -> {} (valid)."""
    path = config_dir / "personas.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}

    personas: dict[str, Persona] = {}
    for name, spec in raw.items():
        prompt = spec.get("prompt")
        prompt_file = spec.get("prompt_file")
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
        personas[name] = Persona(name=name, model=spec.get("model"), system_prompt=system_prompt)
    return personas
