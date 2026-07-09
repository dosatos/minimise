"""Tests for `mini persona` (list / show)."""

import pytest
from click.testing import CliRunner

from minimise.interfaces.cli import mini


@pytest.fixture
def runner():
    return CliRunner()


def test_list_shows_builtins_tagged(runner, mock_config_dir):
    result = runner.invoke(mini, ["persona", "list"])
    assert result.exit_code == 0
    for p in ("rigor", "consistency", "density", "clarity"):
        assert f"mini:doc-review:{p}" in result.output
    assert "BUILTIN" in result.output


def test_list_hides_versioned_aliases(runner, mock_config_dir):
    # Only bare `latest` names are listed, not the @vN aliases.
    result = runner.invoke(mini, ["persona", "list"])
    assert result.exit_code == 0
    assert "@v1" not in result.output


def test_list_shows_user_persona_tagged(runner, mock_config_dir):
    (mock_config_dir / "personas.yaml").write_text(
        "coder:\n  prompt: you are a careful coder\n"
    )
    result = runner.invoke(mini, ["persona", "list"])
    assert result.exit_code == 0
    assert "USER" in result.output
    assert "coder" in result.output


def test_show_bare_name(runner, mock_config_dir):
    result = runner.invoke(mini, ["persona", "show", "mini:doc-review:rigor"])
    assert result.exit_code == 0
    assert "ANALYTICAL RIGOR" in result.output


def test_show_pinned_version(runner, mock_config_dir):
    bare = runner.invoke(mini, ["persona", "show", "mini:doc-review:rigor"])
    pinned = runner.invoke(mini, ["persona", "show", "mini:doc-review:rigor@v1"])
    assert pinned.exit_code == 0
    assert "ANALYTICAL RIGOR" in pinned.output
    assert pinned.output == bare.output


def test_show_unknown_name_nonzero(runner, mock_config_dir):
    result = runner.invoke(mini, ["persona", "show", "nope:not:real"])
    assert result.exit_code != 0
