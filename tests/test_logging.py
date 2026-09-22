"""Unit and integration tests for RingBufferLogHandler and /api/logs endpoint."""

import logging
import pytest
from fastapi.testclient import TestClient

from config.logging_buffer import RingBufferLogHandler, global_log_buffer
from dashboard.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_ring_buffer_log_handler():
    """Verify log handler records records and respects maxlen."""
    handler = RingBufferLogHandler(maxlen=5)
    test_logger = logging.getLogger("test_buffer_logger")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    test_logger.info("Test message 1")
    test_logger.warning("Test warning 2")
    test_logger.error("Test error 3")

    logs = handler.get_logs()
    assert len(logs) == 3
    assert logs[0]["message"] == "Test message 1"
    assert logs[0]["level"] == "INFO"
    assert logs[1]["level"] == "WARNING"
    assert logs[2]["level"] == "ERROR"

    # Test level filter
    warn_logs = handler.get_logs(level="WARNING")
    assert len(warn_logs) == 1
    err_logs = handler.get_logs(level="ERROR")
    assert len(err_logs) == 1

    # Test maxlen rotation
    for i in range(10):
        test_logger.info(f"Rotation msg {i}")

    logs_after = handler.get_logs()
    assert len(logs_after) == 5  # strictly bounded by maxlen


def test_api_logs_endpoint(client):
    """Test GET /api/logs returns buffered logs."""
    logger = logging.getLogger("dashboard")
    logger.setLevel(logging.INFO)
    logger.info("API test log message for buffer check")

    resp = client.get("/api/logs?limit=50")
    assert resp.status_code == 200

    data = resp.json()
    assert "logs" in data
    assert "total" in data
    assert any("API test log message" in log["message"] for log in data["logs"])

