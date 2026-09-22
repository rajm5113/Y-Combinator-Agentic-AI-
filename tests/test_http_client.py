"""Tests for the Resilient Async HTTP Client.

Validates retry logic, politeness delay enforcement, timeout handling,
and graceful failure after max retries — all with mocked responses.
"""

import asyncio
import time

import httpx
import pytest
import pytest_asyncio

from agents.http_client import ResilientHTTPClient, HTTPResult


# ─── Helpers ────────────────────────────────────────────────────────────────

class MockTransport(httpx.AsyncBaseTransport):
    """Custom transport that returns a sequence of predefined responses."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1

            if isinstance(resp, Exception):
                raise resp

            return resp

        # Default fallback: 200 OK
        return httpx.Response(200, text="ok")


def make_client_with_transport(responses: list, **kwargs) -> ResilientHTTPClient:
    """Creates a ResilientHTTPClient backed by a mock transport."""
    transport = MockTransport(responses)
    max_retries = kwargs.pop("max_retries", 3)
    inter_request_delay = kwargs.pop("inter_request_delay", 0.0)
    client = ResilientHTTPClient(
        inter_request_delay=inter_request_delay,
        max_retries=max_retries,
        retry_backoff_base=0.01,  # Near-instant backoff in tests
        **kwargs,
    )
    # Inject the mocked httpx client directly
    client._client = httpx.AsyncClient(transport=transport)
    return client


# ─── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_successful_get():
    """A simple 200 OK response is returned correctly."""
    responses = [
        httpx.Response(200, json={"name": "Kailash Labs"}, headers={"content-type": "application/json"}),
    ]
    client = make_client_with_transport(responses)
    result = await client.get("https://example.com/api")

    assert result.success is True
    assert result.status_code == 200
    assert result.data == {"name": "Kailash Labs"}
    assert result.attempts == 1
    assert result.error is None
    await client.close()


@pytest.mark.asyncio
async def test_http_successful_post():
    """A POST request with JSON body returns parsed response."""
    responses = [
        httpx.Response(200, json={"hits": [1, 2, 3]}, headers={"content-type": "application/json"}),
    ]
    client = make_client_with_transport(responses)
    result = await client.post("https://example.com/search", json={"query": "test"})

    assert result.success is True
    assert result.data == {"hits": [1, 2, 3]}
    await client.close()


@pytest.mark.asyncio
async def test_http_retries_on_429():
    """HTTP 429 triggers retry with backoff, succeeds on second attempt."""
    responses = [
        httpx.Response(429, text="Rate limited"),
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    ]
    client = make_client_with_transport(responses)
    result = await client.get("https://example.com/api")

    assert result.success is True
    assert result.status_code == 200
    assert result.attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_http_retries_on_503():
    """HTTP 503 triggers retry, succeeds on third attempt."""
    responses = [
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(200, json={"recovered": True}, headers={"content-type": "application/json"}),
    ]
    client = make_client_with_transport(responses)
    result = await client.get("https://example.com/api")

    assert result.success is True
    assert result.attempts == 3
    await client.close()


@pytest.mark.asyncio
async def test_http_max_retries_exhausted():
    """After max_retries failures, returns failed HTTPResult without infinite loop."""
    responses = [
        httpx.Response(429, text="Rate limited"),
        httpx.Response(429, text="Rate limited"),
        httpx.Response(429, text="Rate limited"),
    ]
    client = make_client_with_transport(responses, max_retries=3)
    result = await client.get("https://example.com/api")

    assert result.success is False
    assert result.attempts == 3
    assert "All 3 attempts failed" in result.error
    await client.close()


@pytest.mark.asyncio
async def test_http_non_retryable_error():
    """HTTP 404 is not retried — returns immediately."""
    responses = [
        httpx.Response(404, text="Not Found"),
    ]
    client = make_client_with_transport(responses)
    result = await client.get("https://example.com/missing")

    assert result.success is False
    assert result.status_code == 404
    assert result.attempts == 1
    await client.close()


@pytest.mark.asyncio
async def test_http_timeout_handling():
    """A timeout exception is caught cleanly and retried."""
    responses = [
        httpx.TimeoutException("Connection timed out"),
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    ]
    client = make_client_with_transport(responses)
    result = await client.get("https://example.com/slow")

    assert result.success is True
    assert result.attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_http_respects_politeness_delay():
    """Two consecutive requests have at least inter_request_delay between them."""
    responses = [
        httpx.Response(200, text="first", headers={"content-type": "text/plain"}),
        httpx.Response(200, text="second", headers={"content-type": "text/plain"}),
    ]
    delay = 0.15  # 150ms
    client = make_client_with_transport(responses, inter_request_delay=delay)

    start = time.monotonic()
    await client.get("https://example.com/1")
    await client.get("https://example.com/2")
    elapsed = time.monotonic() - start

    assert elapsed >= delay, f"Expected >= {delay}s between requests, got {elapsed:.3f}s"
    await client.close()


@pytest.mark.asyncio
async def test_http_returns_text_for_html():
    """Non-JSON content-type returns raw text."""
    html = "<html><body>Hello</body></html>"
    responses = [
        httpx.Response(200, text=html, headers={"content-type": "text/html"}),
    ]
    client = make_client_with_transport(responses)
    result = await client.get("https://example.com/page")

    assert result.success is True
    assert result.data == html
    assert result.is_json is False
    await client.close()


@pytest.mark.asyncio
async def test_http_context_manager():
    """Client can be used as an async context manager."""
    responses = [
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    ]
    transport = MockTransport(responses)
    client = ResilientHTTPClient(inter_request_delay=0.0, retry_backoff_base=0.01)
    client._client = httpx.AsyncClient(transport=transport)

    async with client:
        result = await client.get("https://example.com")
        assert result.success is True

    # Client should be closed after context exit
    assert client._client.is_closed
