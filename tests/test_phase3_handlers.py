"""Tests for Phase 3 Tool Handlers (Fit and Outreach schemas)."""

from unittest.mock import AsyncMock

import pytest

from db.connection import get_db_connection
from db.memory import memory_manager
from db.models import FitEvaluation, MessageDrafts
from db.storage import StorageEngine, storage_engine
from tools.engine import tool_engine
from tools.handlers.fit_handlers import handle_evaluate_startup_fit
from tools.handlers.outreach_handlers import (
    handle_check_never_contact_blacklist,
    handle_generate_grounded_messages,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean cache and DB for all Phase 3 handler tests."""
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


# ─── Schema Auto-Registration Tests ────────────────────────────────────────

def test_phase3_tools_auto_registered():
    """Verify fit and outreach schemas and handlers are registered."""
    import tools.handlers  # noqa: F401

    assert "evaluate_startup_fit" in tool_engine._handlers
    assert "check_never_contact_blacklist" in tool_engine._handlers
    assert "generate_grounded_messages" in tool_engine._handlers

    assert tool_engine.get_tool_schema("evaluate_startup_fit") is not None
    assert tool_engine.get_tool_schema("check_never_contact_blacklist") is not None
    assert tool_engine.get_tool_schema("generate_grounded_messages") is not None


# ─── Fit Handler Tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_evaluate_startup_fit_success():
    """Test evaluate_startup_fit handler with mocked FitAgent."""
    mock_agent = AsyncMock()
    mock_agent.evaluate_startup.return_value = (
        FitEvaluation(
            startup_id=1,
            score=90,
            fit_tier="HIGH",
            should_contact=True,
            match_rationale=["Strong match in AI infrastructure"],
            contribution_angle="Accelerate core engine",
        ),
        None,
        False,
        False,
    )

    res = await handle_evaluate_startup_fit(
        company_name="Kailash Labs",
        one_liner="Video reasoning models",
        agent=mock_agent,
    )

    assert res["success"] is True
    assert res["result"]["score"] == 90
    assert res["result"]["fit_tier"] == "HIGH"


@pytest.mark.asyncio
async def test_handle_evaluate_startup_fit_validation_error():
    """Test evaluate_startup_fit rejects empty company name or one_liner."""
    res = await handle_evaluate_startup_fit(company_name="", one_liner="Some product")
    assert res["success"] is False
    assert res["error_type"] == "validation_error"


# ─── Outreach Handler Tests ────────────────────────────────────────────────

def test_handle_check_blacklist_clean_and_blocked():
    """Test check_never_contact_blacklist handler correctly reports state."""
    # Clean check
    res_clean = handle_check_never_contact_blacklist(
        founder_name="Jane Smith",
        company_name="Kailash Labs",
    )
    assert res_clean["success"] is True
    assert res_clean["result"]["is_blacklisted"] is False

    # Blacklisted check
    storage_engine.add_to_blacklist(
        identifier_type="founder_name",
        identifier_value="Bad Actor",
        reason="Requested no contact",
    )
    res_blocked = handle_check_never_contact_blacklist(
        founder_name="Bad Actor",
        company_name="Acme",
    )
    assert res_blocked["success"] is True
    assert res_blocked["result"]["is_blacklisted"] is True


@pytest.mark.asyncio
async def test_handle_generate_grounded_messages_success():
    """Test generate_grounded_messages handler produces validated drafts."""
    mock_agent = AsyncMock()
    mock_agent.draft_for_startup_and_founder.return_value = (
        MessageDrafts(
            startup_id=1,
            founder_id=1,
            linkedin_note="Hi Jane, loved your video reasoning models!",
            yc_job_note="Saw you are hiring a founding engineer.",
            cold_email_subject="Video reasoning architectures",
            cold_email_body="Hi Jane, would love to chat.",
        ),
        None,
        False,
        False,
    )

    res = await handle_generate_grounded_messages(
        founder_name="Jane Smith",
        company_name="Kailash Labs",
        verified_facts=["Video reasoning models", "Hiring early team"],
        pitch_angle="Lead foundational AI systems",
        agent=mock_agent,
    )

    assert res["success"] is True
    assert "linkedin_note" in res["result"]
    assert "Kailash" in res["result"]["linkedin_note"] or "video" in res["result"]["linkedin_note"]


@pytest.mark.asyncio
async def test_handle_generate_grounded_messages_validation_error():
    """Test generate_grounded_messages rejects missing required arguments."""
    res = await handle_generate_grounded_messages(
        founder_name="Jane",
        company_name="Kailash",
        verified_facts=[],  # empty facts
        pitch_angle="Lead engineer",
    )
    assert res["success"] is False
    assert res["error_type"] == "validation_error"
