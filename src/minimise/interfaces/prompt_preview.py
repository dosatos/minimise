"""Read a worker's prompt for a collapsed preview on the detail pages.

Every preview is a dict with label, name, source, size_label (files only),
kind ("file" | "persona" | "default"), and exactly one of html / error. A bad
source yields an error notice instead of raising, so a prompt never breaks
the page.
"""

from pathlib import Path
from typing import Optional

import markdown

from minimise.orchestration.loop_engine import DEFAULT_PROMPTS

MAX_PREVIEW_BYTES = 256 * 1024


def render_prompt_markdown(text: str) -> str:
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def _size_label(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def _preview(kind: str, label: str, name: str, source: str,
             size_label: Optional[str] = None, html: Optional[str] = None,
             error: Optional[str] = None) -> dict:
    return {
        "kind": kind,
        "label": label,
        "name": name,
        "source": source,
        "size_label": size_label,
        "html": html,
        "error": error,
    }


def prompt_file_preview(path: str) -> dict:
    """Read the path as-is, like AgentFactory.resolve_prompt_for_worker."""
    file_path = Path(path)

    def failed(error: str, size_label: Optional[str] = None) -> dict:
        return _preview("file", "Prompt file", file_path.name, path,
                        size_label=size_label, error=error)

    if not file_path.is_absolute():
        # The loop's working directory isn't stored, so any base dir is a guess.
        return failed("Relative path: the loop's working directory isn't recorded, "
                      "so this file can't be located.")
    try:
        size = file_path.stat().st_size
    except FileNotFoundError:
        return failed(f"Prompt file not found: {path}")
    except OSError as exc:
        return failed(f"Prompt file could not be read: {exc}")

    size_label = _size_label(size)
    if size > MAX_PREVIEW_BYTES:
        return failed(f"Prompt file is too large to preview ({round(size / 1024)} KB).",
                      size_label)
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return failed(f"Prompt file could not be read: {exc}", size_label)
    return _preview("file", "Prompt file", file_path.name, path,
                    size_label=size_label, html=render_prompt_markdown(text))


def persona_preview(name: str, personas: Optional[dict],
                    load_error: Optional[str] = None) -> dict:
    def failed(error: str) -> dict:
        return _preview("persona", "Persona prompt", name, name, error=error)

    if load_error:
        return failed(f"Personas could not be loaded: {load_error}")
    persona = (personas or {}).get(name)
    if persona is None:
        return failed(f"Unknown persona '{name}'")
    return _preview("persona", "Persona prompt", name, name,
                    html=render_prompt_markdown(persona.system_prompt))


def default_preview(step_type: str) -> dict:
    return _preview(
        "default", "Built-in default prompt", step_type,
        f"Built into minimise for the {step_type} step: used when the worker "
        "sets no persona, prompt or prompt_file.",
        html=render_prompt_markdown(DEFAULT_PROMPTS[step_type]),
    )


def worker_prompt_preview(worker, step_type: str, personas: Optional[dict],
                          load_error: Optional[str] = None) -> Optional[dict]:
    """The preview for a worker, or None for an inline prompt (already visible)."""
    if worker.persona:
        return persona_preview(worker.persona, personas, load_error)
    if worker.prompt:
        return None
    if worker.prompt_file:
        return prompt_file_preview(worker.prompt_file)
    return default_preview(step_type)
