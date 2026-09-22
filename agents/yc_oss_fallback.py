"""YC-OSS public API fallback for startup discovery.

When Algolia credentials are completely unavailable (YC is down, both live
and refreshed keys rejected), this module fetches company data from the
yc-oss/api project — a community-maintained, daily-regenerated JSON API
hosted on GitHub Pages.

Endpoint pattern:
    https://yc-oss.github.io/api/batches/{batch-slug}.json
    e.g. fall-2026.json, winter-2025.json, summer-2025.json

The JSON response is an array of company objects with fields identical to
Algolia hits: name, slug, website, one_liner, long_description, team_size,
batch, tags, industries, subindustry, status, isHiring, etc.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("yc_oss_fallback")

# ─── Constants ──────────────────────────────────────────────────────────────

YC_OSS_BASE_URL = "https://yc-oss.github.io/api/batches"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15.0


# ─── Batch Name → Slug Conversion ──────────────────────────────────────────

def batch_name_to_slug(batch_name: str) -> str:
    """Convert a long-form batch name to a YC-OSS URL slug.

    Examples:
        'Fall 2026'   → 'fall-2026'
        'Summer 2025' → 'summer-2025'
        'Winter 2024' → 'winter-2024'

    If the input is already a slug (lowercase with hyphen), returns it as-is.
    If the input is a short code (F26, S25, W24), converts via long form first.
    """
    # Handle short codes first
    SHORT_TO_LONG = {
        "F26": "Fall 2026", "S26": "Summer 2026", "W26": "Winter 2026",
        "F25": "Fall 2025", "S25": "Summer 2025", "W25": "Winter 2025",
        "F24": "Fall 2024", "S24": "Summer 2024", "W24": "Winter 2024",
    }
    name = SHORT_TO_LONG.get(batch_name, batch_name)

    # Convert "Fall 2026" → "fall-2026"
    return name.lower().replace(" ", "-")


# ─── YC-OSS Fetcher ────────────────────────────────────────────────────────

async def fetch_yc_oss_batch(
    batch_name: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Fetch all companies for a single batch from the YC-OSS API.

    Args:
        batch_name: Batch name in any format ("Fall 2026", "F26", "fall-2026").
        http_client: Optional async HTTP client. Creates one if not provided.

    Returns:
        List of company dicts (same schema as Algolia hits).
        Returns empty list on any failure.
    """
    slug = batch_name_to_slug(batch_name)
    url = f"{YC_OSS_BASE_URL}/{slug}.json"

    own_client = False
    if http_client is None:
        http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        own_client = True

    try:
        response = await http_client.get(
            url,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

        if response.status_code == 404:
            logger.warning(f"YC-OSS batch not found: {slug} (404)")
            return []

        if response.status_code != 200:
            logger.warning(
                f"YC-OSS returned {response.status_code} for batch {slug}"
            )
            return []

        companies = response.json()
        if not isinstance(companies, list):
            logger.warning(f"YC-OSS unexpected response format for batch {slug}")
            return []

        logger.info(
            f"YC-OSS fallback: fetched {len(companies)} companies "
            f"for batch {slug}"
        )
        return companies

    except httpx.HTTPError as e:
        logger.warning(f"HTTP error fetching YC-OSS batch {slug}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching YC-OSS batch {slug}: {e}")
        return []
    finally:
        if own_client:
            await http_client.aclose()


async def fetch_yc_oss_batches(
    batch_names: List[str],
    http_client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Fetch companies for multiple batches from the YC-OSS API.

    Args:
        batch_names: List of batch names in any format.
        http_client: Optional async HTTP client.

    Returns:
        Combined list of company dicts from all batches.
    """
    own_client = False
    if http_client is None:
        http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        own_client = True

    all_companies: List[Dict[str, Any]] = []

    try:
        for batch_name in batch_names:
            companies = await fetch_yc_oss_batch(batch_name, http_client)
            all_companies.extend(companies)
    finally:
        if own_client:
            await http_client.aclose()

    logger.info(
        f"YC-OSS fallback total: {len(all_companies)} companies "
        f"across {len(batch_names)} batches"
    )
    return all_companies
