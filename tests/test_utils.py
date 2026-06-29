import os
from pathlib import Path
from minimise.utils import run_shell_command, project_env


def test_run_shell_command_uses_env(tmp_path):
    ok, out = run_shell_command("echo $MINI_MARKER",
                                env={**os.environ, "MINI_MARKER": "xyz"})
    assert ok and "xyz" in out


def test_project_env_detects_dotvenv(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    env = project_env(tmp_path)
    assert env is not None
    assert env["PATH"].startswith(str(venv_bin) + os.pathsep)
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_project_env_none_when_no_venv(tmp_path):
    assert project_env(tmp_path) is None
