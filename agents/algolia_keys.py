"""Dynamic Algolia credential extraction from YC's live /companies page.

YC rotates its public Algolia API key periodically. This module fetches
the current key at runtime from window.AlgoliaOpts embedded in the HTML,
ensuring the discovery pipeline never breaks due to stale credentials.

The key is a base64-encoded Algolia "secured API key" and must be used
as-is (NOT decoded) in the X-Algolia-API-Key header.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("algolia_keys")

# ─── Constants ──────────────────────────────────────────────────────────────

YC_COMPANIES_URL = "https://www.ycombinator.com/companies"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Cache credentials for 1 hour (YC rotates keys ~daily, so 1h is safe)
KEY_CACHE_TTL_SECONDS = 3600


# ─── Exceptions ─────────────────────────────────────────────────────────────

class YCAlgoliaUnavailable(Exception):
    """Raised when Algolia credentials cannot be obtained.

    The discovery pipeline should catch this and fall back to the
    YC-OSS public API (yc-oss.github.io/api).
    """
    pass


# ─── Data Structures ───────────────────────────────────────────────────────

@dataclass
class AlgoliaCredentials:
    """Holds a live set of Algolia credentials extracted from YC."""

    app_id: str        # e.g. "45BWZJ1SGC" — always uppercase
    api_key: str       # raw base64-encoded secured key
    fetched_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.fetched_at

    @property
    def is_expired(self) -> bool:
        return self.age_seconds > KEY_CACHE_TTL_SECONDS

    def algolia_url(self, index: str = "YCCompany_production") -> str:
        """Build the Algolia search endpoint URL."""
        return f"https://{self.app_id}-dsn.algolia.net/1/indexes/{index}/query"

    def algolia_headers(self) -> dict:
        """Build Algolia API request headers."""
        return {
            "X-Algolia-Application-Id": self.app_id,
            "X-Algolia-API-Key": self.api_key,
            "Content-Type": "application/json",
        }


# ─── Extraction Logic ──────────────────────────────────────────────────────

def extract_algolia_opts_from_html(html: str) -> Optional[AlgoliaCredentials]:
    """Extract window.AlgoliaOpts from raw HTML and return credentials.

    The YC companies page embeds credentials in a <script> tag as:
        window.AlgoliaOpts = {"app":"45BWZJ1SGC","key":"<base64-secured-key>"}

    The key is a base64-encoded Algolia secured API key that includes
    embedded restrictions (restrictIndices, tagFilters, etc.). It must be
    used as-is (NOT base64-decoded) in the API key header.

    Returns None if extraction fails.
    """
    if not html:
        return None

    # Match window.AlgoliaOpts = {...}
    pattern = r'window\.AlgoliaOpts\s*=\s*(\{[^}]+\})'
    match = re.search(pattern, html)
    if not match:
        logger.warning("window.AlgoliaOpts not found in HTML")
        return None

    try:
        opts = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to parse AlgoliaOpts JSON: {e}")
        return None

    app_id = opts.get("app")
    api_key = opts.get("key")

    if not app_id or not api_key:
        logger.warning(f"AlgoliaOpts missing app or key: {list(opts.keys())}")
        return None

    logger.info(
        f"Extracted Algolia credentials: app_id={app_id}, "
        f"key_length={len(api_key)}"
    )
    return AlgoliaCredentials(app_id=app_id, api_key=api_key)


async def fetch_live_algolia_credentials(
    http_client: Optional[httpx.AsyncClient] = None,
) -> Optional[AlgoliaCredentials]:
    """Fetch fresh Algolia credentials from the live YC /companies page.

    Uses an async HTTP client. Creates a temporary one if none is provided.
    Returns None on any failure (network, parsing, etc.).
    """
    own_client = False
    if http_client is None:
        http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        own_client = True

    try:
        response = await http_client.get(
            YC_COMPANIES_URL,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        if response.status_code != 200:
            logger.warning(
                f"YC /companies returned status {response.status_code}"
            )
            return None

        return extract_algolia_opts_from_html(response.text)

    except httpx.HTTPError as e:
        logger.warning(f"HTTP error fetching YC /companies: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching Algolia credentials: {e}")
        return None
    finally:
        if own_client:
            await http_client.aclose()


# ─── Credential Manager (Singleton Cache) ──────────────────────────────────

class AlgoliaCredentialManager:
    """Manages cached Algolia credentials with automatic refresh.

    Thread-safe singleton that:
    1. Caches credentials for KEY_CACHE_TTL_SECONDS (1 hour).
    2. On expiry or first access, fetches fresh credentials from YC.
    3. Raises YCAlgoliaUnavailable if live extraction fails.

    The caller (DiscoveryAgent) handles the fallback to YC-OSS API.
    """

    _instance: Optional["AlgoliaCredentialManager"] = None
    _cached_creds: Optional[AlgoliaCredentials] = None

    def __new__(cls) -> "AlgoliaCredentialManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_credentials(
        self,
        force_refresh: bool = False,
    ) -> AlgoliaCredentials:
        """Get current Algolia credentials, refreshing if expired.

        Args:
            force_refresh: Force a fresh fetch regardless of cache state.

        Returns:
            AlgoliaCredentials from live YC page extraction.

        Raises:
            YCAlgoliaUnavailable: If live credentials cannot be obtained.
                The caller should fall back to YC-OSS public API.
        """
        # Return cached if still valid
        if (
            not force_refresh
            and self._cached_creds is not None
            and not self._cached_creds.is_expired
        ):
            logger.debug(
                f"Using cached Algolia credentials "
                f"(age: {self._cached_creds.age_seconds:.0f}s)"
            )
            return self._cached_creds

        # Try live fetch
        logger.info("Fetching fresh Algolia credentials from YC...")
        live_creds = await fetch_live_algolia_credentials()

        if live_creds is not None:
            self._cached_creds = live_creds
            logger.info(
                f"Live Algolia credentials obtained: "
                f"app_id={live_creds.app_id}"
            )
            return live_creds

        # No credentials available — caller should use YC-OSS fallback
        raise YCAlgoliaUnavailable(
            "Unable to extract Algolia credentials from YC /companies page. "
            "The discovery pipeline will fall back to the YC-OSS public API."
        )

    def invalidate(self) -> None:
        """Force credential refresh on next access."""
        self._cached_creds = None
        logger.info("Algolia credential cache invalidated")


# Module-level singleton
credential_manager = AlgoliaCredentialManager()
