"""Unit and API tests for pipeline run tracking, history, cancellation, and re-run configuration."""

import pytest
from fastapi.testclient import TestClient

from db.storage import storage_engine
from pipeline import MasterOrchestrator, PipelineConfig, PipelineStage
from dashboard.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_pipeline_run_persistence():
    """Verify storing, updating, and querying pipeline runs."""
    session_id = "test_pipe_session_123"

    # 1. Start run
    run_id = storage_engine.record_pipeline_run_start(
        session_id=session_id,
        batch="Fall 2026",
        industry="B2B",
        startup_limit=10,
        min_fit_score=60,
        max_concurrency=4,
        dry_run=False,
    )
    assert run_id > 0

    # 2. Update progress
    updated = storage_engine.update_pipeline_run_progress(
        session_id=session_id,
        progress_message="Evaluating fit...",
        status="running",
    )
    assert updated is True

    # 3. Complete run
    completed = storage_engine.record_pipeline_run_complete(
        session_id=session_id,
        status="completed",
        duration_seconds=12.5,
        stats={"discovered": 10, "qualified": 4, "drafts": 4},
    )
    assert completed is True

    # 4. Check last run config
    last_cfg = storage_engine.get_last_pipeline_run_config()
    assert last_cfg is not None
    assert last_cfg["batch"] == "Fall 2026"
    assert last_cfg["industry"] == "B2B"
    assert last_cfg["startup_limit"] == 10
    assert last_cfg["min_fit_score"] == 60
    assert last_cfg["max_concurrency"] == 4

    # 5. Check run history
    history = storage_engine.get_pipeline_run_history(limit=5)
    assert len(history) >= 1
    item = next(h for h in history if h["session_id"] == session_id)
    assert item["status"] == "completed"
    assert item["duration_seconds"] == 12.5
    assert item["stats_json"]["qualified"] == 4


@pytest.mark.asyncio
async def test_orchestrator_cancellation():
    """Verify MasterOrchestrator gracefully halts when cancelled."""
    config = PipelineConfig(
        batches=["Fall 2026"],
        dry_run=True,
    )
    orch = MasterOrchestrator(config=config)

    # Trigger cancel before running
    orch.cancel()
    assert orch.cancel_event.is_set()

    report = await orch.run()
    assert report.success is False

    # Check status recorded in DB is 'cancelled'
    history = storage_engine.get_pipeline_run_history(limit=5)
    run_rec = next(h for h in history if h["session_id"] == orch.session_id)
    assert run_rec["status"] == "cancelled"


def test_api_pipeline_last_config_and_history(client):
    """Test GET /api/pipeline/last-config and GET /api/pipeline/history."""
    # Insert a run to ensure history is populated
    storage_engine.record_pipeline_run_start(
        session_id="api_test_session_456",
        batch="Summer 2026",
        industry="AI",
        startup_limit=8,
        min_fit_score=70,
        max_concurrency=3,
        dry_run=True,
    )
    storage_engine.record_pipeline_run_complete(
        session_id="api_test_session_456",
        status="completed",
        duration_seconds=5.0,
    )

    # GET /api/pipeline/last-config
    resp = client.get("/api/pipeline/last-config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["batch"] == "Summer 2026"
    assert cfg["startup_limit"] == 8
    assert cfg["min_fit_score"] == 70

    # GET /api/pipeline/history
    resp2 = client.get("/api/pipeline/history?limit=10")
    assert resp2.status_code == 200
    history = resp2.json()
    assert len(history) >= 1
    assert any(h["session_id"] == "api_test_session_456" for h in history)

    # POST /api/pipeline/cancel when none running
    resp3 = client.post("/api/pipeline/cancel")
    assert resp3.status_code == 200
    assert resp3.json()["success"] is False  # No active pipeline
