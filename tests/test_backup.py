"""Unit and integration tests for BackupEngine and backup endpoints."""

import gzip
import json
import pytest
from fastapi.testclient import TestClient

from db.backup import BackupEngine, backup_engine
from db.models import FounderCreate, StartupCreate
from db.storage import storage_engine
from dashboard.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_backup_create_and_restore(tmp_path):
    """Verify end-to-end backup snapshot generation and transactional restoration."""
    engine = BackupEngine(backup_dir=tmp_path)

    # Insert test data into SQLite test db
    s_id = storage_engine.upsert_startup(StartupCreate(
        name="BackupTest Co",
        slug="backuptest-co",
        batch="Fall 2026",
        industry="Enterprise",
        website="https://backuptest.com",
    ))
    storage_engine.upsert_founder(FounderCreate(
        startup_id=s_id,
        full_name="Test Founder",
        linkedin_url="https://linkedin.com/in/testfounder",
    ))

    # Create backup
    res = engine.create_backup()
    assert res["success"] is True
    assert res["filename"].endswith(".json.gz")
    assert res["size_bytes"] > 0
    assert "startups" in res["table_counts"]
    assert res["table_counts"]["startups"] >= 1

    # Verify physical file
    backup_file = tmp_path / res["filename"]
    assert backup_file.exists()

    with gzip.open(backup_file, "rt", encoding="utf-8") as f:
        payload = json.load(f)
        assert "metadata" in payload
        assert "tables" in payload
        assert "startups" in payload["tables"]
        names = [r["name"] for r in payload["tables"]["startups"]]
        assert "BackupTest Co" in names

    # Test list_backups
    backups = engine.list_backups()
    assert len(backups) >= 1
    assert backups[0]["filename"] == res["filename"]

    # Test restore
    restore_res = engine.restore_backup(res["filename"])
    assert restore_res["success"] is True
    assert "restored_counts" in restore_res

    # Verify data exists after restore
    startups = storage_engine.get_all_startups()
    assert any(s["name"] == "BackupTest Co" for s in startups)


def test_api_backup_endpoints(client, tmp_path, monkeypatch):
    """Test REST API endpoints for backups."""
    # Point backup_engine to tmp_path
    monkeypatch.setattr(backup_engine, "backup_dir", tmp_path)

    # 1. POST /api/backup
    resp = client.post("/api/backup")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    filename = data["filename"]

    # 2. GET /api/backups
    resp2 = client.get("/api/backups")
    assert resp2.status_code == 200
    backups = resp2.json()
    assert len(backups) >= 1
    assert backups[0]["filename"] == filename

    # 3. POST /api/backups/restore
    resp3 = client.post("/api/backups/restore", json={"filename": filename})
    assert resp3.status_code == 200
    assert resp3.json()["success"] is True
