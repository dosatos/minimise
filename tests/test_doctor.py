"""Tests for `mini doctor` — harness health, provider auth, settings display."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

import pytest
from click.testing import CliRunner

import minimise.interfaces.cli as _cli
from minimise.interfaces.cli.doctor import doctor, _HARNESS_BINS, _PROVIDER_KEYS


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def temp_config():
    """A clean config dir with no settings.yaml / personas.yaml.

    No settings.yaml means load_settings returns defaults (claude, no model).
    """
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d)
        (cfg / "jobs").mkdir()
        yield cfg


# ── helpers ──────────────────────────────────────────────────────────────

def _fake_which_all_installed(binary):
    return f"/usr/local/bin/{binary}"


def _fake_run_ok(cmd, **kwargs):
    proc = MagicMock()
    proc.stdout = f"{cmd[0]} v1.0.0"
    proc.stderr = ""
    return proc


# ── harness health: only the resolved harness gates healthy ──────────────

@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_claude_installed_pi_missing_still_healthy(mock_which, mock_run,
                                                    runner, temp_config, monkeypatch):
    """When claude is the resolved harness (default), a missing pi binary
    should NOT make doctor unhealthy."""
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = lambda binary: (
        "/usr/local/bin/claude" if binary == "claude" else None
    )
    mock_run.side_effect = _fake_run_ok

    # pi auth.json exists → healthy
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    result = runner.invoke(doctor, catch_exceptions=False)
    assert result.exit_code == 0


@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_pi_resolved_but_pi_missing_is_unhealthy(mock_which, mock_run,
                                                  runner, temp_config, monkeypatch):
    """When pi is the resolved harness (via settings), a missing pi binary
    SHOULD make doctor unhealthy."""
    (temp_config / "settings.yaml").write_text("version: \"0.0.1\"\nharness: pi\n")
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = lambda binary: (
        "/usr/local/bin/claude" if binary == "claude" else None  # pi not found
    )
    mock_run.side_effect = _fake_run_ok

    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    result = runner.invoke(doctor, catch_exceptions=False)
    assert result.exit_code == 1


@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_env_var_overrides_settings_for_resolved_harness(mock_which, mock_run,
                                                          runner, temp_config, monkeypatch):
    """MINIMISE_HARNESS=claude should resolve claude even when settings says pi."""
    (temp_config / "settings.yaml").write_text("version: \"0.0.1\"\nharness: pi\n")
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)
    monkeypatch.setenv("MINIMISE_HARNESS", "claude")

    mock_which.side_effect = lambda binary: (
        "/usr/local/bin/claude" if binary == "claude" else None
    )
    mock_run.side_effect = _fake_run_ok

    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    result = runner.invoke(doctor, catch_exceptions=False)
    # pi is missing but resolved harness is claude → healthy
    assert result.exit_code == 0


# ── provider auth: expanded key list + auth.json fallback ────────────────

@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_all_provider_keys_listed_in_table(mock_which, mock_run,
                                            runner, temp_config, monkeypatch):
    """Every key in _PROVIDER_KEYS should appear in the table."""
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    mock_which.side_effect = _fake_which_all_installed
    mock_run.side_effect = _fake_run_ok

    result = runner.invoke(doctor, catch_exceptions=False)
    output = result.stdout

    # Spot-check a few that were NOT in the old 3-key list
    assert "OPENROUTER_API_KEY" in output
    assert "GROQ_API_KEY" in output
    assert "MISTRAL_API_KEY" in output
    assert "DEEPSEEK_API_KEY" in output
    assert "HF_TOKEN" in output
    assert "AZURE_OPENAI_API_KEY" in output
    # The original 3 should still be there
    assert "ANTHROPIC_API_KEY" in output
    assert "OPENAI_API_KEY" in output
    assert "GOOGLE_API_KEY" in output


@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_auth_json_makes_healthy_without_env_vars(mock_which, mock_run,
                                                   runner, temp_config, monkeypatch):
    """When no provider env vars are set but ~/.pi/agent/auth.json exists,
    doctor should still report healthy."""
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = _fake_which_all_installed
    mock_run.side_effect = _fake_run_ok

    # auth.json exists
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    # Clear any provider env vars
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    result = runner.invoke(doctor, catch_exceptions=False)
    assert result.exit_code == 0


@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_no_env_vars_and_no_auth_json_is_unhealthy(mock_which, mock_run,
                                                    runner, temp_config, monkeypatch):
    """When no provider env vars are set AND auth.json doesn't exist,
    doctor should report unhealthy."""
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = _fake_which_all_installed
    mock_run.side_effect = _fake_run_ok

    # auth.json does NOT exist
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    # Clear any provider env vars
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    result = runner.invoke(doctor, catch_exceptions=False)
    assert result.exit_code == 1


@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_any_provider_env_var_makes_healthy(mock_which, mock_run,
                                             runner, temp_config, monkeypatch):
    """Setting any single provider env var should make doctor healthy,
    even without auth.json."""
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = _fake_which_all_installed
    mock_run.side_effect = _fake_run_ok

    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    # Clear all, then set just one
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test123")

    result = runner.invoke(doctor, catch_exceptions=False)
    assert result.exit_code == 0


# ── settings display ─────────────────────────────────────────────────────

@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_settings_table_shows_harness_and_model(mock_which, mock_run,
                                                 runner, temp_config, monkeypatch):
    """The active settings table shows the harness and model from settings.yaml."""
    (temp_config / "settings.yaml").write_text(
        "version: \"0.0.1\"\nharness: pi\nmodel: gpt-5\n"
    )
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = _fake_which_all_installed
    mock_run.side_effect = _fake_run_ok

    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    result = runner.invoke(doctor, catch_exceptions=False)
    output = result.stdout
    assert "Active Settings" in output
    assert "pi" in output
    assert "gpt-5" in output


@patch("minimise.interfaces.cli.doctor.subprocess.run")
@patch("minimise.interfaces.cli.doctor.shutil.which")
def test_persona_overrides_table(mock_which, mock_run,
                                  runner, temp_config, monkeypatch):
    """Persona overrides with harness/model should appear in the output."""
    (temp_config / "settings.yaml").write_text("version: \"0.0.1\"\n")
    (temp_config / "personas.yaml").write_text("""
reviewer:
  model: claude-opus-4-8
  prompt: "Review carefully."
""")
    monkeypatch.setattr(_cli, "CONFIG_DIR", temp_config)

    mock_which.side_effect = _fake_which_all_installed
    mock_run.side_effect = _fake_run_ok

    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "auth.json")
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    result = runner.invoke(doctor, catch_exceptions=False)
    output = result.stdout
    assert "Persona Overrides" in output
    assert "reviewer" in output
    assert "claude-opus-4-8" in output
