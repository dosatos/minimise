import os

import pytest

from minimise.interfaces import prompt_preview
from minimise.interfaces.prompt_preview import (
    default_preview,
    persona_preview,
    prompt_file_preview,
    render_prompt_markdown,
    worker_prompt_preview,
)
from minimise.models import Worker
from minimise.orchestration.loop_engine import DEFAULT_PROMPTS
from minimise.personas import Persona

KEYS = {"kind", "label", "name", "source", "size_label", "html", "error"}


def _assert_one_of_html_or_error(preview):
    assert set(preview) == KEYS
    assert (preview["html"] is None) != (preview["error"] is None)


def test_render_prompt_markdown_supports_fenced_code_and_tables():
    html = render_prompt_markdown(
        "# Title\n\n```python\nx = 1\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    assert "<h1>Title</h1>" in html
    assert "<code" in html and "x = 1" in html
    assert "<table>" in html and "<td>1</td>" in html


def test_prompt_file_preview_renders_absolute_file(tmp_path):
    path = tmp_path / "planner.md"
    path.write_text("# Planner heading\n" + "x" * 2100)

    preview = prompt_file_preview(str(path))

    _assert_one_of_html_or_error(preview)
    assert preview["label"] == "Prompt file"
    assert preview["name"] == "planner.md"
    assert preview["source"] == str(path)
    assert preview["size_label"] == "2.1 KB"
    assert "<h1>Planner heading</h1>" in preview["html"]


def test_prompt_file_preview_small_file_size_in_bytes(tmp_path):
    path = tmp_path / "p.md"
    path.write_text("hello")
    assert prompt_file_preview(str(path))["size_label"] == "5 B"


def test_prompt_file_preview_relative_path_is_not_guessed(tmp_path, monkeypatch):
    # Even when the file exists relative to cwd, a relative path isn't resolved.
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "implement.md").write_text("# exists")
    monkeypatch.chdir(tmp_path)

    preview = prompt_file_preview("prompts/implement.md")

    _assert_one_of_html_or_error(preview)
    assert preview["error"] == (
        "Relative path: the loop's working directory isn't recorded, "
        "so this file can't be located."
    )
    assert preview["name"] == "implement.md"
    assert preview["source"] == "prompts/implement.md"


def test_prompt_file_preview_does_not_expand_user():
    preview = prompt_file_preview("~/prompts/planner.md")
    assert preview["error"].startswith("Relative path")


def test_prompt_file_preview_missing_file(tmp_path):
    path = tmp_path / "gone.md"

    preview = prompt_file_preview(str(path))

    _assert_one_of_html_or_error(preview)
    assert preview["error"] == f"Prompt file not found: {path}"
    assert preview["size_label"] is None


def test_prompt_file_preview_too_large_checks_size_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "big.md"
    path.write_bytes(b"a" * (300 * 1024))

    def no_read(*args, **kwargs):
        raise AssertionError("oversized file must not be read")

    monkeypatch.setattr(prompt_preview.Path, "read_text", no_read)

    preview = prompt_file_preview(str(path))

    _assert_one_of_html_or_error(preview)
    assert preview["error"] == "Prompt file is too large to preview (300 KB)."


def test_prompt_file_preview_at_limit_still_renders(tmp_path):
    path = tmp_path / "edge.md"
    path.write_bytes(b"a" * prompt_preview.MAX_PREVIEW_BYTES)
    assert prompt_file_preview(str(path))["html"] is not None


def test_prompt_file_preview_undecodable_file(tmp_path):
    path = tmp_path / "binary.md"
    path.write_bytes(b"\xff\xfe\x00bad")

    preview = prompt_file_preview(str(path))

    _assert_one_of_html_or_error(preview)
    assert preview["error"].startswith("Prompt file could not be read: ")


def test_prompt_file_preview_directory_is_unreadable(tmp_path):
    preview = prompt_file_preview(str(tmp_path))
    assert preview["error"].startswith("Prompt file could not be read: ")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_prompt_file_preview_permission_denied(tmp_path):
    path = tmp_path / "locked.md"
    path.write_text("secret")
    path.chmod(0)
    try:
        preview = prompt_file_preview(str(path))
    finally:
        path.chmod(0o644)
    assert preview["error"].startswith("Prompt file could not be read: ")


def test_persona_preview_renders_known_persona():
    personas = {"writer": Persona(name="writer", system_prompt="## Be brief")}

    preview = persona_preview("writer", personas)

    _assert_one_of_html_or_error(preview)
    assert preview["label"] == "Persona prompt"
    assert preview["name"] == "writer"
    assert preview["source"] == "writer"
    assert preview["size_label"] is None
    assert "<h2>Be brief</h2>" in preview["html"]


def test_persona_preview_unknown_persona():
    preview = persona_preview("ghost", {})

    _assert_one_of_html_or_error(preview)
    assert preview["error"] == "Unknown persona 'ghost'"


def test_persona_preview_load_error_wins():
    personas = {"writer": Persona(name="writer", system_prompt="x")}

    preview = persona_preview("writer", personas, load_error="bad yaml")

    _assert_one_of_html_or_error(preview)
    assert preview["error"] == "Personas could not be loaded: bad yaml"


@pytest.mark.parametrize("step_type", ["plan", "implement", "evaluate"])
def test_default_preview_renders_loop_engine_default(step_type):
    preview = default_preview(step_type)

    _assert_one_of_html_or_error(preview)
    assert preview["label"] == "Built-in default prompt"
    assert preview["name"] == step_type
    assert preview["html"] == render_prompt_markdown(DEFAULT_PROMPTS[step_type])


def test_default_preview_uses_loop_engine_dict(monkeypatch):
    monkeypatch.setitem(DEFAULT_PROMPTS, "plan", "# Patched default")
    assert "<h1>Patched default</h1>" in default_preview("plan")["html"]


def test_worker_prompt_preview_dispatches_by_worker_setting(tmp_path):
    path = tmp_path / "p.md"
    path.write_text("# File")
    personas = {"writer": Persona(name="writer", system_prompt="# Persona")}

    assert worker_prompt_preview(Worker(persona="writer"), "plan", personas)["label"] == (
        "Persona prompt"
    )
    assert worker_prompt_preview(Worker(prompt_file=str(path)), "plan", personas)["label"] == (
        "Prompt file"
    )
    assert worker_prompt_preview(Worker(prompt="inline"), "plan", personas) is None
    default = worker_prompt_preview(Worker(), "evaluate", None)
    assert default["label"] == "Built-in default prompt"
    assert default["name"] == "evaluate"


def test_worker_prompt_preview_passes_load_error_to_persona():
    preview = worker_prompt_preview(Worker(persona="writer"), "plan", None, "boom")
    assert preview["error"] == "Personas could not be loaded: boom"
