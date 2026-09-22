"""YC Startup Discovery Agent — queries YC Algolia API, filters by batch and industry,
deduplicates via StorageEngine, and stages Startup records in Business Memory.

Three-tier discovery strategy:
  1. Algolia with dynamically extracted key from YC /companies page.
  2. On 403 → refresh key from YC → retry once.
  3. On persistent failure → YC-OSS public API fallback (yc-oss.github.io/api).

Zero DOM scraping. Zero fragile selectors.
"""

import json
import logging
import math
from typing import Any, Dict, List, Optional, Set

from agents.algolia_keys import AlgoliaCredentials, YCAlgoliaUnavailable, credential_manager
from agents.base import AgentResult, BaseAgent
from agents.http_client import ResilientHTTPClient
from agents.yc_oss_fallback import fetch_yc_oss_batches
from config.settings import settings
from db.memory import memory_manager
from db.models import StartupCreate
from db.storage import storage_engine

logger = logging.getLogger("discovery_agent")

# ─── Batch Normalization ────────────────────────────────────────────────────

BATCH_SHORT_TO_LONG = {
    "F26": "Fall 2026", "S26": "Summer 2026", "W26": "Winter 2026",
    "F25": "Fall 2025", "S25": "Summer 2025", "W25": "Winter 2025",
    "F24": "Fall 2024", "S24": "Summer 2024", "W24": "Winter 2024",
}

BATCH_LONG_TO_SHORT = {v: k for k, v in BATCH_SHORT_TO_LONG.items()}


def normalize_batch_to_long(batch: str) -> str:
    """Normalizes a batch code to long format (e.g., 'F26' → 'Fall 2026').

    YC's secured Algolia key requires long-format batch names in facet filters.
    Short codes (e.g., 'F26') return 0 hits with the secured key.
    """
    return BATCH_SHORT_TO_LONG.get(batch, batch)


def normalize_batch_to_short(batch: str) -> str:
    """Normalizes a batch name to short format (e.g., 'Fall 2026' → 'F26')."""
    return BATCH_LONG_TO_SHORT.get(batch, batch)


# ─── Industry Filtering ────────────────────────────────────────────────────

def is_relevant_startup(hit: Dict[str, Any], target_industries: List[str]) -> bool:
    """Returns True if the startup overlaps with at least one target industry.

    If target_industries is empty, returns True (inclusive by default).
    """
    if not target_industries:
        return True

    startup_tags = set(t.lower() for t in hit.get("tags", []))
    startup_industries = set(i.lower() for i in hit.get("industries", []))
    all_labels = startup_tags | startup_industries
    if hit.get("subindustry"):
        all_labels.add(hit["subindustry"].lower())

    target_set = set(t.lower() for t in target_industries)
    return bool(all_labels & target_set)


# ─── Algolia Hit → StartupCreate Conversion ────────────────────────────────

def algolia_hit_to_startup(hit: Dict[str, Any]) -> StartupCreate:
    """Converts an Algolia search hit into a typed StartupCreate model."""
    batch_raw = hit.get("batch", "")
    batch_long = normalize_batch_to_long(batch_raw)

    return StartupCreate(
        name=hit.get("name", "Unknown"),
        slug=hit.get("slug", hit.get("objectID", "unknown")),
        batch=batch_long,
        website=hit.get("website"),
        one_liner=hit.get("one_liner"),
        long_description=hit.get("long_description"),
        team_size=hit.get("team_size"),
        industry=hit.get("subindustry") or (hit.get("industries", [None])[0] if hit.get("industries") else None),
        subindustry=hit.get("subindustry"),
        tags=hit.get("tags", []),
        status=hit.get("status", "active").lower(),
        is_hiring=bool(hit.get("isHiring", False)),
        yc_url=f"{settings.yc_base_url}/companies/{hit.get('slug', '')}",
    )


# ─── Discovery Agent ───────────────────────────────────────────────────────

class DiscoveryAgent(BaseAgent):
    """Discovers YC startups via Algolia with YC-OSS fallback.

    Three-tier discovery strategy:
    1. Check Cache Memory for identical query results (TTL: 24h).
    2. Fetch live Algolia credentials from YC /companies page (cached 1h).
    3. Build Algolia POST request with batch facet filters (long format).
    4. Paginate through results if necessary.
    5. Filter by target industries.
    6. Deduplicate via StorageEngine upsert.
    7. On 403, invalidate credentials, refresh, and retry once.
    8. If Algolia is completely unavailable, fall back to YC-OSS public API.
    9. Cache results and return AgentResult.
    """

    agent_name = "discovery"
    description = "Discovers newly funded YC startups from the Algolia public directory API"
    required_tools = ["search_yc_directory", "fetch_batch_startups"]

    HITS_PER_PAGE = 50
    CACHE_TTL = 86400  # 24 hours

    def __init__(self, http_client: Optional[ResilientHTTPClient] = None):
        self.http_client = http_client

    async def _get_http_client(self) -> ResilientHTTPClient:
        """Lazily creates or returns the HTTP client."""
        if self.http_client is None:
            self.http_client = ResilientHTTPClient()
        return self.http_client

    async def _get_algolia_credentials(self, force_refresh: bool = False) -> AlgoliaCredentials:
        """Get Algolia credentials via the credential manager.

        Raises YCAlgoliaUnavailable if live extraction fails.
        """
        return await credential_manager.get_credentials(
            force_refresh=force_refresh,
        )

    def _build_algolia_body(
        self,
        batch: str,
        page: int = 0,
        hits_per_page: int = 50,
    ) -> Dict[str, Any]:
        """Builds the Algolia search request body.

        IMPORTANT: batch must be in LONG format (e.g., 'Fall 2026').
        The secured API key does not work with short codes.
        """
        return {
            "query": "",
            "facetFilters": [[f"batch:{batch}"]],
            "hitsPerPage": hits_per_page,
            "page": page,
            "attributesToRetrieve": [
                "name", "slug", "website", "one_liner", "long_description",
                "team_size", "batch", "tags", "industries", "subindustry",
                "status", "isHiring", "objectID", "small_logo_thumb_url",
            ],
        }

    async def _query_algolia_batch(
        self,
        client: ResilientHTTPClient,
        creds: AlgoliaCredentials,
        batch_long: str,
        industries: List[str],
        all_startups: List[Dict[str, Any]],
        stats: Dict[str, Any],
        errors: List[str],
        limit: Optional[int],
    ) -> bool:
        """Query Algolia for a single batch, paginating through all results.

        Returns True if successful (even with 0 hits), False if a 403 occurred
        (indicating credentials need refresh).
        """
        url = creds.algolia_url(settings.algolia_index)
        headers = creds.algolia_headers()
        page = 0
        total_pages = 1

        while page < total_pages:
            body = self._build_algolia_body(batch_long, page=page, hits_per_page=self.HITS_PER_PAGE)
            result = await client.post(url, json=body, headers=headers)

            if not result.success:
                error_msg = f"Algolia API error for batch {batch_long} page {page}: {result.error}"
                logger.warning(error_msg)
                errors.append(error_msg)

                if result.status_code in (403, 400):
                    return False  # Signal credential refresh needed
                break

            response_data = result.data
            if not isinstance(response_data, dict):
                errors.append(f"Unexpected Algolia response format for batch {batch_long}")
                break

            hits = response_data.get("hits", [])
            total_pages = response_data.get("nbPages", 1)
            stats["total_algolia_hits"] += len(hits)

            for hit in hits:
                if not is_relevant_startup(hit, industries):
                    continue

                stats["passed_industry_filter"] += 1

                try:
                    startup = algolia_hit_to_startup(hit)
                    startup_id = storage_engine.upsert_startup(startup)

                    startup_record = {
                        "startup_id": startup_id,
                        "slug": startup.slug,
                        "name": startup.name,
                        "batch": startup.batch,
                        "is_hiring": startup.is_hiring,
                        "industry": startup.industry,
                        "tags": startup.tags,
                    }
                    all_startups.append(startup_record)

                except Exception as e:
                    error_msg = f"Failed to upsert startup {hit.get('slug', '?')}: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

                if limit and len(all_startups) >= limit:
                    break

            page += 1

            if limit and len(all_startups) >= limit:
                break

        return True

    async def _discover_via_yc_oss(
        self,
        batches: List[str],
        industries: List[str],
        all_startups: List[Dict[str, Any]],
        stats: Dict[str, Any],
        errors: List[str],
        limit: Optional[int],
    ) -> None:
        """Fallback: fetch companies from the YC-OSS public API.

        The YC-OSS API returns data with the same field schema as Algolia,
        so algolia_hit_to_startup() works on both sources.
        """
        logger.info("[discovery] Falling back to YC-OSS public API...")
        batch_longs = [normalize_batch_to_long(b) for b in batches]

        oss_companies = await fetch_yc_oss_batches(batch_longs)

        if not oss_companies:
            errors.append(
                "YC-OSS fallback also returned no data. "
                "Both Algolia and YC-OSS are unavailable."
            )
            return

        stats["source"] = "yc-oss"
        stats["total_algolia_hits"] = len(oss_companies)  # Reuse field for total

        for hit in oss_companies:
            if not is_relevant_startup(hit, industries):
                continue

            stats["passed_industry_filter"] += 1

            try:
                startup = algolia_hit_to_startup(hit)
                startup_id = storage_engine.upsert_startup(startup)

                startup_record = {
                    "startup_id": startup_id,
                    "slug": startup.slug,
                    "name": startup.name,
                    "batch": startup.batch,
                    "is_hiring": startup.is_hiring,
                    "industry": startup.industry,
                    "tags": startup.tags,
                }
                all_startups.append(startup_record)

            except Exception as e:
                error_msg = f"Failed to upsert startup {hit.get('slug', '?')}: {e}"
                logger.warning(error_msg)
                errors.append(error_msg)

            if limit and len(all_startups) >= limit:
                break

        logger.info(
            f"[discovery] YC-OSS fallback: {len(all_startups)} startups "
            f"from {len(oss_companies)} total companies"
        )

    async def execute(self, context: Dict[str, Any]) -> AgentResult:
        """Main discovery execution with three-tier fallback.

        Strategy:
            1. Try Algolia with dynamic live key
            2. On 403 → refresh key → retry Algolia
            3. Still fails → YC-OSS public API fallback

        Context parameters:
            batches: List[str] — YC batch names or codes (e.g., ["Fall 2026", "F26"])
            industries: List[str] — Target industries to filter (empty = return all)
            limit: int — Maximum total startups to return (default: unlimited)
        """
        batches = context.get("batches", settings.default_batches)
        industries = context.get("industries", settings.default_industries)
        limit = context.get("limit")

        stats = {
            "source": "algolia",
            "total_algolia_hits": 0,
            "passed_industry_filter": 0,
            "new_startups_inserted": 0,
            "existing_startups_updated": 0,
            "cached_hits": 0,
        }
        errors: List[str] = []
        all_startups: List[Dict[str, Any]] = []

        # ── Cache check ──
        cache_params = {"batches": sorted(batches), "industries": sorted(industries)}
        cached = memory_manager.get_cached_tool_result("search_yc_directory", cache_params)
        if cached:
            stats["cached_hits"] = len(cached) if isinstance(cached, list) else 1
            logger.info(f"[discovery] Cache HIT — returning {stats['cached_hits']} cached startups")
            return AgentResult(
                success=True,
                agent_name=self.agent_name,
                data=cached,
                stats=stats,
            )

        # ── Tier 1: Get live Algolia credentials ──
        client = await self._get_http_client()
        use_yc_oss = False

        try:
            creds = await self._get_algolia_credentials()
        except YCAlgoliaUnavailable as e:
            logger.warning(f"[discovery] Algolia credentials unavailable: {e}")
            use_yc_oss = True

        if not use_yc_oss:
            logger.info(
                f"[discovery] Using Algolia credentials: "
                f"app_id={creds.app_id}, key_age={creds.age_seconds:.0f}s"
            )

            # ── Tier 1 + 2: Query Algolia per batch ──
            for batch in batches:
                # Always use long format — secured key requires it
                batch_long = normalize_batch_to_long(batch)

                success = await self._query_algolia_batch(
                    client, creds, batch_long, industries,
                    all_startups, stats, errors, limit,
                )

                if not success:
                    # ── Tier 2: 403/400 — refresh key and retry once ──
                    logger.warning(
                        f"Algolia returned 403/400 for batch {batch_long}. "
                        f"Refreshing credentials and retrying..."
                    )
                    credential_manager.invalidate()
                    try:
                        creds = await self._get_algolia_credentials(force_refresh=True)
                    except YCAlgoliaUnavailable:
                        logger.warning(
                            "Credential refresh also failed. "
                            "Falling back to YC-OSS API."
                        )
                        use_yc_oss = True
                        break

                    # Clear the 403 error and retry this batch
                    errors = [e for e in errors if "403" not in e and "400" not in e]
                    retry_success = await self._query_algolia_batch(
                        client, creds, batch_long, industries,
                        all_startups, stats, errors, limit,
                    )
                    if not retry_success:
                        # ── Tier 3: Both keys rejected — use YC-OSS ──
                        logger.warning(
                            "Refreshed Algolia key also rejected. "
                            "Falling back to YC-OSS API."
                        )
                        use_yc_oss = True
                        # Clear Algolia errors since we're switching source
                        errors = []
                        all_startups = []
                        stats["total_algolia_hits"] = 0
                        stats["passed_industry_filter"] = 0
                        break

                if limit and len(all_startups) >= limit:
                    break

        # ── Tier 3: YC-OSS Public API Fallback ──
        if use_yc_oss:
            await self._discover_via_yc_oss(
                batches, industries, all_startups, stats, errors, limit,
            )

        # ── Stats ──
        stats["new_startups_inserted"] = stats["passed_industry_filter"]

        # ── Cache results ──
        if all_startups:
            memory_manager.cache_tool_result(
                "search_yc_directory", cache_params, all_startups, ttl=self.CACHE_TTL
            )

        return AgentResult(
            success=len(all_startups) > 0 or len(errors) == 0,
            agent_name=self.agent_name,
            data=all_startups,
            errors=errors,
            stats=stats,
        )

