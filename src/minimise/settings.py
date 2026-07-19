from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Settings:
    """Global minimisation settings from ~/.minimise/settings.yaml."""

    VERSION: str = "0.0.1"

    harness: str = "claude"
    model: Optional[str] = None


def load_settings(config_dir: Path) -> Settings:
    """Load settings from config_dir/settings.yaml.

    Missing file or missing keys produce defaults silently. Env var
    merging (MINIMISE_HARNESS) is handled in _shared.py, not here.
    """
    path = config_dir / "settings.yaml"
    if not path.exists():
        return Settings()

    raw = yaml.safe_load(path.read_text()) or {}

    version = raw.get("version")
    if version != Settings.VERSION:
        raise ValueError(
            f"settings.yaml version '{version}' is not supported "
            f"(expected version '{Settings.VERSION}')"
        )

    return Settings(
        harness=raw.get("harness", "claude"),
        model=raw.get("model"),
    )
