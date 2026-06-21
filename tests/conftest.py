import pytest
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
