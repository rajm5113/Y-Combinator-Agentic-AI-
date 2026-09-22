"""Root pytest configuration and safety fixtures.

Ensures all tests run in an isolated temporary SQLite database
and never touch or mutate the live production PostgreSQL database or Redis cache.
"""

import pytest
from config.settings import settings
from db.storage import StorageEngine


@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path, monkeypatch):
    """Isolates the database to a per-test temporary SQLite instance.
    
    Prevents unit tests from wiping or inserting test fixture records (like 'Lead AI')
    into the active development/production PostgreSQL database.
    """
    monkeypatch.setattr(settings, "database_url", "")
    test_db = tmp_path / "pytest_isolated.db"
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    StorageEngine.init_db()
    yield
