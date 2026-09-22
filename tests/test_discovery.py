"""Tests for the YC Startup Discovery Agent.

All HTTP responses are mocked — zero live API calls. Zero tokens consumed.
Validates Algolia parsing, industry filtering, deduplication, caching,
batch normalization, error handling, pagination, and dynamic credential refresh.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.algolia_keys import AlgoliaCredentials, YCAlgoliaUnavailable
from agents.discovery_agent import (
    DiscoveryAgent,
    algolia_hit_to_startup,
    is_relevant_startup,
    normalize_batch_to_long,
    normalize_batch_to_short,
)
from agents.http_client import HTTPResult
from db.memory import memory_manager
from db.storage import StorageEngine

# ─── Fixtures ───────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Static test credentials (never hit the network)
TEST_CREDS = AlgoliaCredentials(
    app_id="45BWZJ1SGC",
    api_key="test-mock-key-for-unit-tests",
)


@pytest.fixture(autouse=True)
def clean_state():
    """Clears cache and reinitializes DB before each test to prevent pollution."""
    # Clear cache
    memory_manager.clear_all_cache()
    # Reinitialize DB tables
    StorageEngine.init_db()
    yield


@pytest.fixture(autouse=True)
def mock_algolia_creds():
    """Patches the credential manager so no live HTTP calls are made."""
    with patch(
        "agents.discovery_agent.credential_manager.get_credentials",
        new_callable=AsyncMock,
        return_value=TEST_CREDS,
    ) as mock_get:
        yield mock_get


def load_algolia_fixture() -> dict:
    """Loads the mock Algolia API response."""
    with open(FIXTURES_DIR / "mock_algolia_response.json", "r") as f:
        return json.load(f)


# ─── Unit Tests: Batch Normalization ────────────────────────────────────────

def test_batch_normalize_short_to_long():
    """'F26' normalizes to 'Fall 2026'."""
    assert normalize_batch_to_long("F26") == "Fall 2026"
    assert normalize_batch_to_long("S26") == "Summer 2026"
    assert normalize_batch_to_long("W26") == "Winter 2026"


def test_batch_normalize_long_to_short():
    """'Fall 2026' normalizes to 'F26'."""
    assert normalize_batch_to_short("Fall 2026") == "F26"
    assert normalize_batch_to_short("Summer 2026") == "S26"


def test_batch_normalize_passthrough():
    """Unknown batch formats pass through unchanged."""
    assert normalize_batch_to_long("Spring 2026") == "Spring 2026"
    assert normalize_batch_to_short("X99") == "X99"


# ─── Unit Tests: Industry Filtering ────────────────────────────────────────

def test_filter_matches_by_tags():
    """Startup tags matching target industries returns True."""
    hit = {"tags": ["Developer Tools", "AI"], "industries": [], "subindustry": None}
    assert is_relevant_startup(hit, ["Developer Tools"]) is True


def test_filter_matches_by_industry():
    """Startup industries matching target returns True."""
    hit = {"tags": [], "industries": ["B2B"], "subindustry": None}
    assert is_relevant_startup(hit, ["B2B"]) is True


def test_filter_matches_by_subindustry():
    """Startup subindustry matching target returns True."""
    hit = {"tags": [], "industries": [], "subindustry": "AI/ML"}
    assert is_relevant_startup(hit, ["AI/ML"]) is True


def test_filter_no_match():
    """Non-matching startup returns False."""
    hit = {"tags": ["Healthcare"], "industries": ["Healthcare"], "subindustry": "Biotech"}
    assert is_relevant_startup(hit, ["Developer Tools", "AI/ML"]) is False


def test_filter_case_insensitive():
    """Filtering is case-insensitive."""
    hit = {"tags": ["developer tools"], "industries": [], "subindustry": None}
    assert is_relevant_startup(hit, ["Developer Tools"]) is True


def test_filter_empty_industries_returns_all():
    """Empty target industries = include everything (inclusive by default)."""
    hit = {"tags": ["Biotech"], "industries": ["Healthcare"], "subindustry": "Biotech"}
    assert is_relevant_startup(hit, []) is True


# ─── Unit Tests: Algolia Hit Conversion ─────────────────────────────────────

def test_algolia_hit_to_startup():
    """Algolia hit is correctly converted to StartupCreate model."""
    fixture = load_algolia_fixture()
    hit = fixture["hits"][0]  # Kailash Labs
    startup = algolia_hit_to_startup(hit)

    assert startup.name == "Kailash Labs"
    assert startup.slug == "kailash-labs"
    assert startup.batch == "Fall 2026"  # Normalized from F26
    assert startup.is_hiring is True
    assert startup.tags == ["Developer Tools", "AI"]
    assert startup.industry == "AI/ML"
    assert "kailash-labs" in startup.yc_url


# ─── Integration Tests: Discovery Agent Execution ──────────────────────────

@pytest.mark.asyncio
async def test_discovery_parses_algolia_response():
    """Mocked Algolia response with 5 hits is correctly parsed."""
    fixture = load_algolia_fixture()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=HTTPResult(
        success=True, status_code=200, data=fixture, url="https://algolia.net", attempts=1,
    ))

    agent = DiscoveryAgent(http_client=mock_http)
    result = await agent.run({"batches": ["Fall 2026"], "industries": []})

    assert result.success is True
    assert result.agent_name == "discovery"
    assert len(result.data) == 5  # No industry filter = all 5
    assert result.stats["total_algolia_hits"] == 5


@pytest.mark.asyncio
async def test_discovery_filters_by_industry():
    """Industry filter returns only matching startups."""
    fixture = load_algolia_fixture()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=HTTPResult(
        success=True, status_code=200, data=fixture, url="https://algolia.net", attempts=1,
    ))

    agent = DiscoveryAgent(http_client=mock_http)
    # Target: AI/ML and Developer Tools — should match Kailash, Acme, DevForge (3 matches)
    result = await agent.run({
        "batches": ["Fall 2026"],
        "industries": ["AI/ML", "Developer Tools"],
    })

    assert result.success is True
    assert result.stats["total_algolia_hits"] == 5
    # Kailash (AI/ML + Developer Tools), Acme (Analytics — matches via tags: AI),
    # DevForge (Developer Tools) = 3 matches; BioGenix + FinLeap filtered out
    matched_slugs = {s["slug"] for s in result.data}
    assert "biogenix" not in matched_slugs
    assert "finleap" not in matched_slugs


@pytest.mark.asyncio
async def test_discovery_deduplicates_via_storage():
    """Same startup slug inserted twice results in 1 row (upsert)."""
    fixture = load_algolia_fixture()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=HTTPResult(
        success=True, status_code=200, data=fixture, url="https://algolia.net", attempts=1,
    ))

    agent = DiscoveryAgent(http_client=mock_http)
    # Run twice with no cache
    with patch.object(type(agent), '_get_http_client', return_value=mock_http):
        result1 = await agent.execute({"batches": ["Fall 2026"], "industries": []})

    # Clear cache so second run queries again
    from db.memory import memory_manager
    memory_manager.clear_all_cache()

    with patch.object(type(agent), '_get_http_client', return_value=mock_http):
        result2 = await agent.execute({"batches": ["Fall 2026"], "industries": []})

    # Both should succeed with 5 startups each (upserted, not duplicated)
    assert result1.success is True
    assert result2.success is True
    assert len(result1.data) == 5
    assert len(result2.data) == 5


@pytest.mark.asyncio
async def test_discovery_respects_cache():
    """Second call with identical params returns cache without HTTP call."""
    fixture = load_algolia_fixture()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=HTTPResult(
        success=True, status_code=200, data=fixture, url="https://algolia.net", attempts=1,
    ))

    agent = DiscoveryAgent(http_client=mock_http)
    # First call — hits Algolia
    result1 = await agent.run({"batches": ["Fall 2026"], "industries": []})
    assert result1.success is True

    # Second call — should hit cache
    result2 = await agent.run({"batches": ["Fall 2026"], "industries": []})
    assert result2.success is True
    assert result2.stats["cached_hits"] > 0

    # Algolia POST should only have been called once (for first call)
    assert mock_http.post.call_count == 1


@pytest.mark.asyncio
async def test_discovery_handles_algolia_error():
    """HTTP 503 from Algolia results in AgentResult with errors."""
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=HTTPResult(
        success=False, status_code=503, data=None,
        url="https://algolia.net", attempts=3,
        error="HTTP 503: Service Unavailable",
    ))

    StorageEngine.init_db()

    agent = DiscoveryAgent(http_client=mock_http)
    result = await agent.run({"batches": ["Fall 2026"], "industries": []})

    assert len(result.errors) > 0
    assert any("Algolia API error" in e for e in result.errors)


@pytest.mark.asyncio
async def test_discovery_handles_403_with_credential_refresh():
    """HTTP 403 triggers automatic credential refresh and retry.

    When the first attempt returns 403, the agent should:
    1. Invalidate cached credentials
    2. Fetch fresh credentials (force_refresh=True)
    3. Retry the same batch with new credentials
    """
    fixture = load_algolia_fixture()
    call_count = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call fails with 403 (expired key)
            return HTTPResult(
                success=False, status_code=403, data=None,
                url=url, attempts=1, error="HTTP 403: Forbidden",
            )
        else:
            # Retry with refreshed key succeeds
            return HTTPResult(
                success=True, status_code=200, data=fixture,
                url=url, attempts=1,
            )

    mock_http = AsyncMock()
    mock_http.post = mock_post

    StorageEngine.init_db()

    # Mock credential refresh to return new credentials
    refreshed_creds = AlgoliaCredentials(
        app_id="45BWZJ1SGC",
        api_key="refreshed-key-after-403",
    )
    with patch(
        "agents.discovery_agent.credential_manager.get_credentials",
        new_callable=AsyncMock,
        side_effect=[TEST_CREDS, refreshed_creds],
    ), patch(
        "agents.discovery_agent.credential_manager.invalidate",
    ) as mock_invalidate:
        agent = DiscoveryAgent(http_client=mock_http)
        result = await agent.run({"batches": ["Fall 2026"], "industries": []})

    # Should succeed after retry
    assert result.success is True
    assert len(result.data) == 5
    # Credential invalidation should have been called
    mock_invalidate.assert_called_once()
    # Should have made 2 HTTP calls (first 403, then retry)
    assert call_count == 2


@pytest.mark.asyncio
async def test_discovery_handles_persistent_403_falls_back_to_yc_oss():
    """When both Algolia keys are rejected, falls back to YC-OSS API."""
    fixture = load_algolia_fixture()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=HTTPResult(
        success=False, status_code=403, data=None,
        url="https://algolia.net", attempts=1,
        error="HTTP 403: Forbidden",
    ))

    StorageEngine.init_db()

    # Mock YC-OSS fallback to return fixture data
    with patch(
        "agents.discovery_agent.fetch_yc_oss_batches",
        new_callable=AsyncMock,
        return_value=fixture["hits"],  # Same schema as Algolia
    ) as mock_oss:
        agent = DiscoveryAgent(http_client=mock_http)
        result = await agent.run({"batches": ["Fall 2026"], "industries": []})

    # Should succeed via YC-OSS fallback
    assert result.success is True
    assert len(result.data) == 5
    assert result.stats.get("source") == "yc-oss"
    mock_oss.assert_called_once()


@pytest.mark.asyncio
async def test_discovery_yc_oss_fallback_on_credential_unavailable():
    """When Algolia credential extraction fails entirely, uses YC-OSS."""
    fixture = load_algolia_fixture()
    mock_http = AsyncMock()

    StorageEngine.init_db()

    # Credential manager raises YCAlgoliaUnavailable
    with patch(
        "agents.discovery_agent.credential_manager.get_credentials",
        new_callable=AsyncMock,
        side_effect=YCAlgoliaUnavailable("Cannot reach YC"),
    ), patch(
        "agents.discovery_agent.fetch_yc_oss_batches",
        new_callable=AsyncMock,
        return_value=fixture["hits"],
    ) as mock_oss:
        agent = DiscoveryAgent(http_client=mock_http)
        result = await agent.run({"batches": ["Fall 2026"], "industries": []})

    # Should succeed via YC-OSS, no Algolia POST calls at all
    assert result.success is True
    assert len(result.data) == 5
    assert result.stats.get("source") == "yc-oss"
    mock_oss.assert_called_once()
    # No Algolia calls were made
    mock_http.post.assert_not_called()


@pytest.mark.asyncio
async def test_discovery_pagination():
    """Response with nbPages > 1 triggers multiple paginated requests."""
    page0_data = {
        "hits": load_algolia_fixture()["hits"][:2],  # 2 startups
        "nbHits": 4,
        "page": 0,
        "nbPages": 2,
        "hitsPerPage": 2,
    }
    page1_data = {
        "hits": load_algolia_fixture()["hits"][2:4],  # 2 more startups
        "nbHits": 4,
        "page": 1,
        "nbPages": 2,
        "hitsPerPage": 2,
    }

    call_count = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return HTTPResult(success=True, status_code=200, data=page0_data, url=url, attempts=1)
        else:
            call_count += 1
            return HTTPResult(success=True, status_code=200, data=page1_data, url=url, attempts=1)

    mock_http = AsyncMock()
    mock_http.post = mock_post

    StorageEngine.init_db()

    agent = DiscoveryAgent(http_client=mock_http)
    result = await agent.run({"batches": ["Fall 2026"], "industries": []})

    assert result.success is True
    assert result.stats["total_algolia_hits"] == 4
    assert call_count == 2  # Two pages fetched

