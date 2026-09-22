"""Resilient async HTTP client with connection pooling, rate limiting, and retry logic.

Separate from the LLM fallback engine — this handles web scraping and API calls
to YC Algolia, YC company pages, and other external data sources.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from config.settings import settings

logger = logging.getLogger("http_client")


@dataclass
class HTTPResult:
    """Standardized HTTP response contract."""

    success: bool
    status_code: int
    data: Any  # Parsed JSON or raw text
    url: str
    attempts: int
    error: Optional[str] = None

    @property
    def is_json(self) -> bool:
        return isinstance(self.data, (dict, list))


class ResilientHTTPClient:
    """Async HTTP client with connection pooling, politeness delay, and retry logic.

    Designed for polite, respectful data harvesting from YC and similar sources.
    Separate rate governance from the LLM client's own backoff engine.
    """

    def __init__(
        self,
        max_connections: Optional[int] = None,
        inter_request_delay: Optional[float] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff_base: float = 1.0,
        jitter_factor: float = 0.25,
        user_agent: str = "YCOutreachBot/1.0 (research; non-commercial)",
    ):
        self.max_connections = max_connections or settings.http_max_connections
        self.inter_request_delay = inter_request_delay if inter_request_delay is not None else settings.http_inter_request_delay
        self.timeout = timeout or settings.http_timeout_seconds
        self.max_retries = max_retries or settings.http_max_retries
        self.retry_backoff_base = retry_backoff_base
        self.jitter_factor = jitter_factor
        self.user_agent = user_agent

        self._last_request_time: float = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initializes the httpx.AsyncClient with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=self.max_connections,
                    max_keepalive_connections=self.max_connections // 2 or 1,
                ),
                timeout=httpx.Timeout(self.timeout),
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
        return self._client

    async def _enforce_politeness_delay(self):
        """Enforces minimum inter-request delay to be respectful to servers."""
        if self.inter_request_delay <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.inter_request_delay:
            wait = self.inter_request_delay - elapsed
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    def _calculate_backoff(self, retry: int) -> float:
        """Exponential backoff with jitter."""
        base_delay = self.retry_backoff_base * (2 ** retry)
        jitter = random.uniform(0, self.jitter_factor * base_delay)
        return base_delay + jitter

    def _is_retryable(self, status_code: int) -> bool:
        """Determines if an HTTP status code warrants a retry."""
        return status_code in (429, 500, 502, 503, 504)

    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> HTTPResult:
        """GET request with retry logic, backoff, and politeness delay."""
        return await self._request("GET", url, headers=headers, params=params)

    async def post(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> HTTPResult:
        """POST request with retry logic (used for Algolia API)."""
        return await self._request("POST", url, headers=headers, json_body=json)

    async def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> HTTPResult:
        """Core request method with retry, backoff, and politeness enforcement."""
        client = await self._get_client()
        last_error: Optional[str] = None
        last_status: int = 0

        for attempt in range(1, self.max_retries + 1):
            await self._enforce_politeness_delay()

            try:
                if method == "GET":
                    response = await client.get(url, headers=headers, params=params)
                elif method == "POST":
                    response = await client.post(url, headers=headers, json=json_body)
                else:
                    return HTTPResult(
                        success=False, status_code=0, data=None,
                        url=url, attempts=attempt,
                        error=f"Unsupported HTTP method: {method}",
                    )

                last_status = response.status_code

                # Success path
                if 200 <= response.status_code < 300:
                    # Try to parse as JSON, fall back to text
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        data = response.json()
                    else:
                        data = response.text

                    return HTTPResult(
                        success=True,
                        status_code=response.status_code,
                        data=data,
                        url=url,
                        attempts=attempt,
                    )

                # Retryable error
                if self._is_retryable(response.status_code):
                    last_error = f"HTTP {response.status_code}: {response.reason_phrase}"
                    logger.warning(
                        f"[HTTP] {method} {url} → {response.status_code} "
                        f"(attempt {attempt}/{self.max_retries}). Retrying..."
                    )
                    if attempt < self.max_retries:
                        backoff = self._calculate_backoff(attempt - 1)
                        await asyncio.sleep(backoff)
                    continue

                # Non-retryable client error (4xx except 429)
                last_error = f"HTTP {response.status_code}: {response.reason_phrase}"
                logger.warning(f"[HTTP] {method} {url} → {response.status_code} (non-retryable)")
                return HTTPResult(
                    success=False,
                    status_code=response.status_code,
                    data=response.text,
                    url=url,
                    attempts=attempt,
                    error=last_error,
                )

            except httpx.TimeoutException as e:
                last_error = f"Timeout after {self.timeout}s: {str(e)}"
                last_status = 0
                logger.warning(
                    f"[HTTP] {method} {url} → Timeout "
                    f"(attempt {attempt}/{self.max_retries})"
                )
                if attempt < self.max_retries:
                    backoff = self._calculate_backoff(attempt - 1)
                    await asyncio.sleep(backoff)

            except httpx.ConnectError as e:
                last_error = f"Connection error: {str(e)}"
                last_status = 0
                logger.warning(
                    f"[HTTP] {method} {url} → Connection error "
                    f"(attempt {attempt}/{self.max_retries})"
                )
                if attempt < self.max_retries:
                    backoff = self._calculate_backoff(attempt - 1)
                    await asyncio.sleep(backoff)

            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
                last_status = 0
                logger.error(f"[HTTP] {method} {url} → Unexpected error: {e}", exc_info=True)
                break  # Don't retry on unknown errors

        # All retries exhausted
        return HTTPResult(
            success=False,
            status_code=last_status,
            data=None,
            url=url,
            attempts=self.max_retries,
            error=f"All {self.max_retries} attempts failed. Last error: {last_error}",
        )

    async def close(self):
        """Gracefully shuts down the HTTP client and releases connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.info("[HTTP] Client connection pool closed.")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
