"""Tests for Phase 2 Tool Handlers and Async ToolEngine execution."""

from unittest.mock import AsyncMock

import pytest

from agents.base import AgentResult
from db.connection import get_db_connection
from db.memory import memory_manager
from db.storage import StorageEngine, storage_engine
from tools.engine import ToolEngine, tool_engine
from tools.handlers.discovery_handlers import (
    handle_fetch_batch_startups,
    handle_search_yc_directory,
)
from tools.handlers.founder_handlers import (
    handle_extract_company_profile,
    handle_verify_founder_socials,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean cache and DB for all handler tests."""
    memory_manager.clear_all_cache()
    StorageEngine.init_db()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM outreach_records")
        conn.execute("DELETE FROM message_drafts")
        conn.execute("DELETE FROM fit_evaluations")
        conn.execute("DELETE FROM founders")
        conn.execute("DELETE FROM startups")
        conn.execute("DELETE FROM blacklist")
    yield
    memory_manager.clear_all_cache()


# ─── Handler Auto-Registration & Schema Tests ──────────────────────────────

def test_handlers_auto_registered():
    """Verify all Phase 2 tool handlers and schemas are automatically loaded."""
    # Ensure handlers are imported and registered
    import tools.handlers  # noqa: F401

    assert "search_yc_directory" in tool_engine._handlers
    assert "fetch_batch_startups" in tool_engine._handlers
    assert "extract_company_profile" in tool_engine._handlers
    assert "verify_founder_socials" in tool_engine._handlers

    # Verify schemas were registered
    assert tool_engine.get_tool_schema("search_yc_directory") is not None
    assert tool_engine.get_tool_schema("extract_company_profile") is not None


# ─── Discovery Handler Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_search_yc_directory_success():
    """Test search_yc_directory handler with mocked agent execution."""
    mock_agent = AsyncMock()
    mock_agent.execute.return_value = AgentResult(
        success=True,
        agent_name="discovery",
        data=[
            {"name": "Kailash Labs", "batch": "Fall 2026", "is_hiring": True},
            {"name": "Acme AI", "batch": "Fall 2026", "is_hiring": False},
        ],
        errors=[],
        stats={},
        duration_seconds=0.5,
    )

    res = await handle_search_yc_directory(
        batch="Fall 2026",
        industry="AI/ML",
        is_hiring=True,
        limit=10,
        agent=mock_agent,
    )

    assert res["success"] is True
    assert res["error"] is None
    assert len(res["result"]) == 1
    assert res["result"][0]["name"] == "Kailash Labs"


@pytest.mark.asyncio
async def test_handle_search_yc_directory_validation_error():
    """Test search_yc_directory fails gracefully on invalid parameters."""
    res = await handle_search_yc_directory(batch="")
    assert res["success"] is False
    assert res["error_type"] == "validation_error"
    assert "non-empty string" in res["error"]


@pytest.mark.asyncio
async def test_handle_fetch_batch_startups_success():
    """Test fetch_batch_startups handler returns batch roster."""
    mock_agent = AsyncMock()
    mock_agent.execute.return_value = AgentResult(
        success=True,
        agent_name="discovery",
        data=[{"name": "Batch Startup", "batch": "F26"}],
        errors=[],
        stats={},
        duration_seconds=0.2,
    )

    res = await handle_fetch_batch_startups(batch="F26", agent=mock_agent)
    assert res["success"] is True
    assert len(res["result"]) == 1
    assert res["result"][0]["name"] == "Batch Startup"


# ─── Founder Handler Tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_extract_company_profile_success():
    """Test extract_company_profile handler returns company dossier."""
    mock_agent = AsyncMock()
    mock_agent.execute.return_value = AgentResult(
        success=True,
        agent_name="founder",
        data=[
            {
                "startup_slug": "kailash-labs",
                "founders": [{"full_name": "Jane Smith"}],
                "jobs": [],
            }
        ],
        errors=[],
        stats={},
        duration_seconds=1.0,
    )

    res = await handle_extract_company_profile(
        company_slug="kailash-labs",
        agent=mock_agent,
    )

    assert res["success"] is True
    assert res["result"]["startup_slug"] == "kailash-labs"
    assert len(res["result"]["founders"]) == 1


@pytest.mark.asyncio
async def test_handle_extract_company_profile_validation_error():
    """Test extract_company_profile rejects empty slug."""
    res = await handle_extract_company_profile(company_slug="  ")
    assert res["success"] is False
    assert res["error_type"] == "validation_error"


def test_handle_verify_founder_socials_valid():
    """Test verify_founder_socials canonicalizes URL and checks clean status."""
    res = handle_verify_founder_socials(
        founder_name="Jane Smith",
        company_name="Kailash Labs",
        linkedin_url="linkedin.com/in/janesmith?trk=ref",
    )

    assert res["success"] is True
    data = res["result"]
    assert data["normalized_linkedin_url"] == "https://www.linkedin.com/in/janesmith"
    assert data["is_valid_linkedin"] is True
    assert data["is_blacklisted"] is False
    assert data["verification_status"] == "verified"


def test_handle_verify_founder_socials_blacklisted():
    """Test verify_founder_socials flags blacklisted entities."""
    storage_engine.add_to_blacklist(
        identifier_type="founder_name",
        identifier_value="Jane Smith",
        reason="Requested no contact",
    )

    res = handle_verify_founder_socials(
        founder_name="Jane Smith",
        company_name="Kailash Labs",
        linkedin_url="https://linkedin.com/in/janesmith",
    )

    assert res["success"] is True
    data = res["result"]
    assert data["is_blacklisted"] is True
    assert data["verification_status"] == "blacklisted"


def test_handle_verify_founder_socials_validation():
    """Test verify_founder_socials validates required inputs."""
    res = handle_verify_founder_socials(founder_name="", company_name="Acme")
    assert res["success"] is False
    assert res["error_type"] == "validation_error"


# ─── ToolEngine Async & Sync Dispatching Tests ─────────────────────────────

@pytest.mark.asyncio
async def test_tool_engine_execute_async():
    """Test calling execute_async on registered async handler."""
    engine = ToolEngine()

    async def sample_async_handler(x: int):
        return {"doubled": x * 2}

    engine.register_handler("double_async", sample_async_handler)
    res = await engine.execute_async("double_async", {"x": 5})

    assert res["success"] is True
    assert res["result"]["doubled"] == 10


def test_tool_engine_sync_execute_bridges_async_handler():
    """Test calling synchronous execute() on an async handler bridges smoothly."""
    engine = ToolEngine()

    async def sample_async_handler(msg: str):
        return f"Echo: {msg}"

    engine.register_handler("echo_async", sample_async_handler)
    res = engine.execute("echo_async", {"msg": "hello"})

    assert res["success"] is True
    assert res["result"] == "Echo: hello"
