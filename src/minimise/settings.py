import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

PRICE_BUCKETS = ("input", "cache_read", "cache_write", "output")


@dataclass
class Prices:
    """The optional prices section: USD per million tokens, for a display-only
    cost estimate in the web UI. Nothing priced is ever stored.

    models maps a model id, or "default", to its four prices; a model without
    an entry of its own is priced at default.
    """

    as_of: str
    models: dict[str, dict[str, float]]


@dataclass
class Settings:
    """Global minimisation settings from ~/.minimise/settings.yaml."""

    VERSION: str = "0.0.1"

    harness: str = "claude"
    model: Optional[str] = None
    prices: Optional[Prices] = None


def _price(value, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ValueError(f"settings.yaml {key} must be a non-negative number, got {value!r}")
    return value


def _parse_prices(raw) -> Optional[Prices]:
    """The prices section, validated; a ValueError names the first bad key."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("settings.yaml prices must be a mapping with as_of and models")
    as_of = raw.get("as_of")
    if isinstance(as_of, date):  # YAML reads an unquoted 2026-10-26 as a date
        as_of = as_of.isoformat()
    if not isinstance(as_of, str) or not as_of.strip():
        raise ValueError("settings.yaml prices.as_of is required: the date the prices are from")
    models = raw.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("settings.yaml prices.models must map model ids (or default) to prices")
    parsed = {}
    for model, entry in models.items():
        key = f"prices.models.{model}"
        if not isinstance(entry, dict):
            raise ValueError(f"settings.yaml {key} must map {', '.join(PRICE_BUCKETS)} to prices")
        for bucket in PRICE_BUCKETS:
            if bucket not in entry:
                raise ValueError(f"settings.yaml {key}.{bucket} is missing")
        parsed[str(model)] = {bucket: _price(entry[bucket], f"{key}.{bucket}")
                              for bucket in PRICE_BUCKETS}
    return Prices(as_of=as_of, models=parsed)


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
        prices=_parse_prices(raw.get("prices")),
    )


def load_prices(config_dir: Optional[Path]) -> Optional[Prices]:
    """The configured prices, read fresh so edits apply without a restart;
    None without a prices section, or without a config dir."""
    return load_settings(config_dir).prices if config_dir is not None else None
