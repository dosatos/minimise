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
