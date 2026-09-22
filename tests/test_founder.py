"""Unit and integration tests for Founder Intel & Social Extraction Agent (Spec 2.2).

Verifies Inertia.js extraction, LinkedIn URL normalization, blacklist pre-checks,
LLM fallback parsing, database upserts, jobs extraction, and caching.
Zero live external API calls.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.founder_agent import (
    FounderAgent,
    extract_inertia_data,
    normalize_linkedin_url,
)
from agents.http_client import HTTPResult
from db.connection import get_db_connection
from db.memory import memory_manager
from db.models import StartupCreate
from db.storage import StorageEngine, storage_engine

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_yc_company_page.html"


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure completely isolated database and clean cache for every test."""
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
def mock_html_content() -> str:
    """Loads the mock YC company page HTML fixture."""
    return FIXTURE_PATH.read_text(encoding="utf-8")


# ─── URL Normalization Tests ───────────────────────────────────────────────

def test_founder_normalizes_linkedin_urls():
    """Tests standardizing varied LinkedIn URL representations into canonical format."""
    cases = [
        ("linkedin.com/in/janesmith", "https://www.linkedin.com/in/janesmith"),
        ("https://linkedin.com/in/janesmith/", "https://www.linkedin.com/in/janesmith"),
        ("https://www.linkedin.com/in/janesmith?trk=some-tracking", "https://www.linkedin.com/in/janesmith"),
        ("http://linkedin.com/in/janesmith", "https://www.linkedin.com/in/janesmith"),
        ("https://www.linkedin.com/in/janesmith#experience", "https://www.linkedin.com/in/janesmith"),
    ]
    for raw, expected in cases:
        assert normalize_linkedin_url(raw) == expected, f"Failed for input: {raw}"


def test_founder_rejects_non_linkedin_urls():
    """Tests that non-LinkedIn or invalid URLs return None."""
    invalid_cases = [
        "https://github.com/janesmith",
        "https://twitter.com/janesmith",
        "not-a-linkedin-url.com",
        "http://phishing-linkedin.com/fake",
        "",
        None,
    ]
    for raw in invalid_cases:
        assert normalize_linkedin_url(raw) is None, f"Expected None for: {raw}"


# ─── Inertia.js Extraction Tests ────────────────────────────────────────────

def test_founder_extracts_inertia_json(mock_html_content: str):
    """Tests extracting structured company and founder data from data-page attribute."""
    data = extract_inertia_data(mock_html_content)
    assert data is not None
    assert "props" in data
    company = data["props"]["company"]
    assert company["name"] == "Kailash Labs"
    assert company["slug"] == "kailash-labs"
    assert len(company["founders"]) == 2
    assert company["founders"][0]["full_name"] == "Jane Smith"
    assert company["founders"][1]["full_name"] == "John Doe"
    assert len(company["jobs"]) == 1


def test_founder_handles_html_encoded_json():
    """Tests parsing Inertia.js data-page with HTML-encoded entities (&quot;)."""
    raw_json = json.dumps({
        "component": "Company",
        "props": {
            "company": {
                "name": "Encoded AI",
                "slug": "encoded-ai",
                "founders": [{"full_name": "Alice Bob", "title": "Founder"}],
            }
        },
    })
    encoded_json = raw_json.replace('"', "&quot;")
    html = f"""<html><body><div id="app" data-page='{encoded_json}'></div></body></html>"""

    data = extract_inertia_data(html)
    assert data is not None
    assert data["props"]["company"]["name"] == "Encoded AI"
    assert data["props"]["company"]["founders"][0]["full_name"] == "Alice Bob"


# ─── Founder Agent Integration Tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_founder_extracts_and_upserts_founders(mock_html_content: str):
    """Verifies end-to-end founder extraction, normalization, and database storage."""
    # Pre-seed startup in storage
    startup_id = storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
    ))

    mock_http = AsyncMock()
    mock_http.get.return_value = HTTPResult(
        success=True,
        status_code=200,
        data=mock_html_content,
        url="https://www.ycombinator.com/companies/kailash-labs",
        attempts=1,
    )

    agent = FounderAgent(http_client=mock_http)
    result = await agent.execute({"startup_slugs": ["kailash-labs"]})

    assert result.success is True
    assert result.stats["startups_processed"] == 1
    assert result.stats["founders_extracted"] == 2
    assert result.stats["founders_new"] == 2
    assert result.stats["linkedin_urls_found"] == 2

    # Verify database persistence and foreign keys
    founders = storage_engine.get_founders_by_startup_id(startup_id)
    assert len(founders) == 2
    names = {f["full_name"] for f in founders}
    assert "Jane Smith" in names
    assert "John Doe" in names

    jane = next(f for f in founders if f["full_name"] == "Jane Smith")
    assert jane["linkedin_url"] == "https://www.linkedin.com/in/janesmith"
    assert jane["title"] == "CEO & Co-Founder"


@pytest.mark.asyncio
async def test_founder_skips_blacklisted_entities(mock_html_content: str):
    """Tests that blacklisted founders are rejected at the input guardrail before insertion."""
    storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
    ))

    # Add Jane Smith to blacklist
    storage_engine.add_to_blacklist(
        identifier_type="founder_name",
        identifier_value="Jane Smith",
        reason="Requested no contact",
    )

    mock_http = AsyncMock()
    mock_http.get.return_value = HTTPResult(
        success=True,
        status_code=200,
        data=mock_html_content,
        url="https://www.ycombinator.com/companies/kailash-labs",
        attempts=1,
    )

    agent = FounderAgent(http_client=mock_http)
    result = await agent.execute({"startup_slugs": ["kailash-labs"]})

    assert result.success is True
    assert result.stats["blacklisted_skips"] == 1
    assert result.stats["founders_extracted"] == 1  # Only John Doe was extracted

    startup_record = storage_engine.get_startup_by_slug("kailash-labs")
    founders = storage_engine.get_founders_by_startup_id(startup_record["id"])
    assert len(founders) == 1
    assert founders[0]["full_name"] == "John Doe"


@pytest.mark.asyncio
async def test_founder_extracts_jobs_and_updates_startup(mock_html_content: str):
    """Tests that job postings from Inertia payload update the startup's jobs_data and is_hiring."""
    storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
        is_hiring=False,
    ))

    mock_http = AsyncMock()
    mock_http.get.return_value = HTTPResult(
        success=True,
        status_code=200,
        data=mock_html_content,
        url="https://www.ycombinator.com/companies/kailash-labs",
        attempts=1,
    )

    agent = FounderAgent(http_client=mock_http)
    result = await agent.execute({"startup_slugs": ["kailash-labs"]})

    assert result.success is True
    startup = storage_engine.get_startup_by_slug("kailash-labs")
    assert startup["is_hiring"] == 1
    assert len(startup["jobs_data"]) == 1
    assert startup["jobs_data"][0]["title"] == "Founding Engineer"


@pytest.mark.asyncio
async def test_founder_caches_company_page(mock_html_content: str):
    """Verifies that subsequent extraction calls for the same slug hit Cache Memory."""
    mock_http = AsyncMock()
    mock_http.get.return_value = HTTPResult(
        success=True,
        status_code=200,
        data=mock_html_content,
        url="https://www.ycombinator.com/companies/kailash-labs",
        attempts=1,
    )

    agent = FounderAgent(http_client=mock_http)

    # First call: hits HTTP
    res1 = await agent.execute({"startup_slugs": ["kailash-labs"]})
    assert res1.stats["cached_hits"] == 0
    assert mock_http.get.call_count == 1

    # Second call: uses cache
    res2 = await agent.execute({"startup_slugs": ["kailash-labs"]})
    assert res2.stats["cached_hits"] == 1
    assert mock_http.get.call_count == 1  # No additional network call


@pytest.mark.asyncio
async def test_founder_llm_fallback_on_missing_data_page():
    """Verifies graceful degradation to LLM fallback when Inertia.js data-page is absent."""
    broken_html = "<html><body><div>Legacy page layout with no data-page</div></body></html>"

    mock_http = AsyncMock()
    mock_http.get.return_value = HTTPResult(
        success=True,
        status_code=200,
        data=broken_html,
        url="https://www.ycombinator.com/companies/legacy-corp",
        attempts=1,
    )

    mock_llm = MagicMock()
    mock_llm.call_with_fallback.return_value = {
        "content": json.dumps({
            "company": {
                "name": "Legacy Corp",
                "founders": [
                    {
                        "full_name": "Bob Vance",
                        "title": "Founder",
                        "bio": "Refrigeration specialist",
                        "linkedin_url": "https://linkedin.com/in/bobvance",
                    }
                ],
                "jobs": [],
            }
        })
    }

    agent = FounderAgent(http_client=mock_http, llm_client=mock_llm)
    result = await agent.execute({"startup_slugs": ["legacy-corp"]})

    assert result.success is True
    assert result.stats["llm_fallback_invocations"] == 1
    assert result.stats["founders_extracted"] == 1
    assert any("used LLM fallback" in err for err in result.errors)

    startup = storage_engine.get_startup_by_slug("legacy-corp")
    assert startup is not None
    founders = storage_engine.get_founders_by_startup_id(startup["id"])
    assert len(founders) == 1
    assert founders[0]["full_name"] == "Bob Vance"


@pytest.mark.asyncio
async def test_founder_auto_discovers_startups_without_founders(mock_html_content: str):
    """Tests executing with empty context automatically discovers unpopulated startups."""
    storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
    ))

    mock_http = AsyncMock()
    mock_http.get.return_value = HTTPResult(
        success=True,
        status_code=200,
        data=mock_html_content,
        url="https://www.ycombinator.com/companies/kailash-labs",
        attempts=1,
    )

    agent = FounderAgent(http_client=mock_http)
    # Empty context: should query storage for startups without founders
    result = await agent.execute({})

    assert result.success is True
    assert result.stats["startups_processed"] == 1
    assert result.stats["founders_extracted"] == 2
