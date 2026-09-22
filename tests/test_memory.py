"""Tests for Session & Cache Memory Manager."""

from db.connection import InMemoryCache
from db.memory import MemoryManager


def test_session_state_lifecycle():
    """Verify session memory stores, retrieves, and clears ephemeral state."""
    cache = InMemoryCache()
    mem = MemoryManager(client=cache)

    session_id = "run-test-101"
    assert mem.set_session_state(session_id, "current_step", "discovery")
    assert mem.set_session_state(session_id, "batch_in_progress", "Fall 2026")

    assert mem.get_session_state(session_id, "current_step") == "discovery"
    assert mem.get_session_state(session_id, "batch_in_progress") == "Fall 2026"

    all_state = mem.get_all_session_state(session_id)
    assert all_state["current_step"] == "discovery"
    assert all_state["batch_in_progress"] == "Fall 2026"

    # Clear session
    assert mem.clear_session(session_id)
    assert mem.get_session_state(session_id, "current_step") is None


def test_tool_result_caching():
    """Verify deterministic tool outputs are cached and retrieved by param hash."""
    cache = InMemoryCache()
    mem = MemoryManager(client=cache)

    tool_name = "search_yc_directory"
    params = {"batch": "Fall 2026", "industry": "B2B"}
    result_data = [{"slug": "acme", "name": "Acme Corp"}]

    # Initially not cached
    assert mem.get_cached_tool_result(tool_name, params) is None

    # Cache tool result
    assert mem.cache_tool_result(tool_name, params, result_data, ttl=3600)

    # Retrieve from cache
    cached = mem.get_cached_tool_result(tool_name, params)
    assert cached is not None
    assert len(cached) == 1
    assert cached[0]["slug"] == "acme"
