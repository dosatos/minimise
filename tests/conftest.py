import os
import pytest
import subprocess
import tempfile
from pathlib import Path
from minimise.database import Database

@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for test databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)

@pytest.fixture
def db(temp_db_dir):
    """Create a test database."""
    db_path = temp_db_dir / "test.db"
    db = Database(db_path)
    db.init_db()
    return db

@pytest.fixture
def mock_config_dir(temp_db_dir, monkeypatch):
    """Mock the minimise config directory for CLI tests."""
    config_dir = temp_db_dir / ".minimise"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Patch the CLI module's CONFIG_DIR, DB_PATH, JOBS_DIR
    monkeypatch.setattr("minimise.cli.CONFIG_DIR", config_dir)
    monkeypatch.setattr("minimise.cli.DB_PATH", config_dir / "minimise.db")
    monkeypatch.setattr("minimise.cli.JOBS_DIR", config_dir / "jobs")

    yield config_dir


@pytest.fixture
def isolated_repo(monkeypatch, tmp_path):
    """Point cli.REPO_PATH at a clean, committed temp git repo.

    `mini job new` validates a clean git tree against cli.REPO_PATH (frozen to
    cwd at import). Without this, tests that invoke `job new` fail whenever the
    real working tree is dirty. This gives them a hermetic, always-clean repo.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (repo / "README.md").write_text("test repo\n")
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env)
    monkeypatch.setattr("minimise.cli.REPO_PATH", repo)
    yield repo
