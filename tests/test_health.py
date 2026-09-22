"""Unit and integration tests for HealthChecker and /api/health endpoint."""

import pytest
from fastapi.testclient import TestClient

from config.health import HealthChecker, health_checker
from dashboard.server import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_health_checker_components():
    """Verify that health_checker probes all 4 core components."""
    checker = HealthChecker()
    res = await checker.check_all()

    assert "status" in res
    assert res["status"] in ("healthy", "degraded", "unhealthy")
    assert "components" in res

    comps = res["components"]
    assert "database" in comps
    assert "redis" in comps
    assert "openrouter" in comps
    assert "pipeline" in comps

    # In test environment with sqlite fixture
    assert comps["database"]["status"] in ("healthy", "degraded")
    assert "latency_ms" in comps["database"]


def test_api_health_endpoint(client):
    """Test the GET /api/health endpoint returns structured component status."""
    resp = client.get("/api/health")
    assert resp.status_code == 200

    data = resp.json()
    assert "status" in data
    assert "components" in data
    assert "version" in data
    assert "environment" in data

    components = data["components"]
    assert "database" in components
    assert "latency_ms" in components["database"]
