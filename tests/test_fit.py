"""Tests for Candidate Matching & Fit Qualification Agent (Spec 3.1).

Validates objective rubric scoring, high/medium/low tier classification,
blacklist pre-check short-circuiting, caching, and database persistence.
Zero live API calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.fit_agent import FitAgent
from db.connection import get_db_connection
from db.memory import memory_manager
from db.models import StartupCreate
from db.storage import StorageEngine, storage_engine

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_fit_response.json"


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean cache and DB for all fit evaluation tests."""
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


@pytest.fixture
def mock_fit_data() -> dict:
    """Loads the mock fit evaluation response."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ─── Fit Agent Unit & Integration Tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_fit_evaluation_high_score(mock_fit_data: dict):
    """Verifies that high-scoring startups receive HIGH tier and should_contact=True."""
    sid = storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
        one_liner="Video reasoning models",
        long_description="Building generative video AI architectures",
        tags=["AI", "Developer Tools"],
        team_size=3,
        is_hiring=True,
    ))

    mock_llm = MagicMock()
    mock_llm.call_with_fallback.return_value = {
        "content": json.dumps(mock_fit_data),
        "model_used": "nvidia/nemotron-3-ultra-550b",
        "attempts": 1,
    }

    agent = FitAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is True
    assert result.stats["startups_evaluated"] == 1
    assert result.stats["high_fit_count"] == 1
    assert len(result.data) == 1

    eval_data = result.data[0]
    assert eval_data["score"] == 88
    assert eval_data["fit_tier"] == "HIGH"
    assert eval_data["should_contact"] is True
    assert len(eval_data["match_rationale"]) >= 2

    # Verify persisted in database
    db_eval = storage_engine.get_fit_evaluation_by_startup_id(sid)
    assert db_eval is not None
    assert db_eval["score"] == 88
    assert db_eval["fit_tier"] == "HIGH"
    assert db_eval["should_contact"] is True


@pytest.mark.asyncio
async def test_fit_evaluation_low_score():
    """Verifies that irrelevant startups score < 50, receiving LOW tier and should_contact=False."""
    sid = storage_engine.upsert_startup(StartupCreate(
        name="BioNano Genomics",
        slug="bionano-genomics",
        batch="Fall 2026",
        one_liner="Wet lab DNA sequencing hardware",
        long_description="Developing biological assays for cell therapy",
        tags=["Biotech", "Hardware"],
        team_size=40,
    ))

    low_fit_response = {
        "score": 28,
        "fit_tier": "LOW",
        "should_contact": False,
        "match_rationale": [
            "Hardware wet-lab domain does not overlap with candidate software/AI background.",
            "Team size exceeds early-stage criteria.",
        ],
        "contribution_angle": "No clear engineering alignment.",
    }

    mock_llm = MagicMock()
    mock_llm.call_with_fallback.return_value = {
        "content": json.dumps(low_fit_response),
        "model_used": "nvidia/nemotron-3-ultra-550b",
        "attempts": 1,
    }

    agent = FitAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is True
    assert result.stats["low_fit_count"] == 1

    eval_data = result.data[0]
    assert eval_data["score"] == 28
    assert eval_data["fit_tier"] == "LOW"
    assert eval_data["should_contact"] is False


@pytest.mark.asyncio
async def test_fit_skips_blacklisted_startup():
    """Verifies that blacklisted startups are rejected at the input gate without LLM calls."""
    sid = storage_engine.upsert_startup(StartupCreate(
        name="Spammy AI",
        slug="spammy-ai",
        batch="Fall 2026",
        website="https://spammyai.com",
    ))

    # Add company to blacklist
    storage_engine.add_to_blacklist(
        identifier_type="company_name",
        identifier_value="Spammy AI",
        reason="Aggressive recruiters",
    )

    mock_llm = MagicMock()
    agent = FitAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is True
    assert result.stats["blacklisted_skips"] == 1
    assert result.stats["startups_evaluated"] == 0
    assert mock_llm.call_with_fallback.call_count == 0  # Zero tokens consumed!


@pytest.mark.asyncio
async def test_fit_evaluation_cached(mock_fit_data: dict):
    """Verifies that subsequent evaluation calls hit Cache Memory."""
    sid = storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
    ))

    mock_llm = MagicMock()
    mock_llm.call_with_fallback.return_value = {
        "content": json.dumps(mock_fit_data),
        "model_used": "nvidia/nemotron-3-ultra-550b",
    }

    agent = FitAgent(llm_client=mock_llm)

    # First call: hits LLM
    res1 = await agent.execute({"startup_id": sid})
    assert res1.stats["cached_hits"] == 0
    assert mock_llm.call_with_fallback.call_count == 1

    # Second call: uses cache
    res2 = await agent.execute({"startup_id": sid})
    assert res2.stats["cached_hits"] == 1
    assert mock_llm.call_with_fallback.call_count == 1  # No additional LLM call


@pytest.mark.asyncio
async def test_fit_evaluates_startups_needing_assessment(mock_fit_data: dict):
    """Verifies auto-discovery of startups without fit evaluations."""
    s1 = storage_engine.upsert_startup(StartupCreate(name="Alpha", slug="alpha", batch="F26"))
    s2 = storage_engine.upsert_startup(StartupCreate(name="Beta", slug="beta", batch="F26"))

    mock_llm = MagicMock()
    mock_llm.call_with_fallback.return_value = {
        "content": json.dumps(mock_fit_data),
        "model_used": "nvidia/nemotron-3-ultra-550b",
    }

    agent = FitAgent(llm_client=mock_llm)
    # Empty context: should discover both s1 and s2
    result = await agent.execute({})

    assert result.success is True
    assert result.stats["startups_evaluated"] == 2
    assert len(result.data) == 2


@pytest.mark.asyncio
async def test_fit_handles_llm_failure_gracefully():
    """Verifies defensive error handling when LLM call fails."""
    sid = storage_engine.upsert_startup(StartupCreate(name="Crash Corp", slug="crash-corp", batch="F26"))

    mock_llm = MagicMock()
    mock_llm.call_with_fallback.side_effect = RuntimeError("OpenRouter 500 server outage")

    agent = FitAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is False
    assert result.stats["evaluation_failures"] == 1
    assert any("OpenRouter 500" in err for err in result.errors)
