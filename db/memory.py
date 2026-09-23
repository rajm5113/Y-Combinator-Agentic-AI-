"""Session Memory & Cache Manager backed by Redis / InMemoryCache.

Manages ephemeral run states, Master-to-Specialist handoffs, and deterministic
tool result caching to prevent redundant token and network consumption.
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional
from db.connection import get_redis_client

logger = logging.getLogger("memory_manager")


class MemoryManager:
    """Manages Session Memory (ephemeral state) and Cache Memory (tool caching)."""

    def __init__(self, client=None):
        self.client = client or get_redis_client()

    @staticmethod
    def hash_params(params: Dict[str, Any]) -> str:
        """Deterministic MD5 hash of tool arguments for cache keying."""
        serialized = json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(serialized.encode("utf-8")).hexdigest()

    # --- Session Memory Methods ---

    def set_session_state(self, session_id: str, key: str, value: Any, ttl: int = 86400) -> bool:
        """Stores a state variable inside the active session context."""
        session_key = f"session:{session_id}"
        serialized = json.dumps(value, default=str)
        try:
            self.client.hset(session_key, key, serialized)
            # Redis hashes do not accept TTL on hset itself; refresh the
            # session key TTL after each write so session state expires.
            if ttl and hasattr(self.client, "expire"):
                self.client.expire(session_key, ttl)
            return True
        except Exception as e:
            logger.error(f"Error setting session state: {e}")
            return False

    def get_session_state(self, session_id: str, key: str) -> Optional[Any]:
        """Retrieves a state variable from the active session context."""
        session_key = f"session:{session_id}"
        try:
            val = self.client.hget(session_key, key)
            if val is not None:
                return json.loads(val)
            return None
        except Exception as e:
            logger.error(f"Error reading session state: {e}")
            return None

    def get_all_session_state(self, session_id: str) -> Dict[str, Any]:
        """Returns the full session context dictionary."""
        session_key = f"session:{session_id}"
        try:
            raw = self.client.hgetall(session_key)
            return {k: json.loads(v) for k, v in raw.items()}
        except Exception as e:
            logger.error(f"Error retrieving all session state: {e}")
            return {}

    def clear_session(self, session_id: str) -> bool:
        """Wipes the session context after a pipeline run completes."""
        session_key = f"session:{session_id}"
        try:
            self.client.delete(session_key)
            return True
        except Exception as e:
            logger.error(f"Error clearing session: {e}")
            return False

    # --- Tool Cache Memory Methods ---

    def cache_tool_result(self, tool_name: str, params: Dict[str, Any], result: Any, ttl: int = 3600) -> bool:
        """Caches a deterministic tool execution result."""
        param_hash = self.hash_params(params)
        cache_key = f"cache:tool:{tool_name}:{param_hash}"
        try:
            serialized = json.dumps(result, default=str)
            self.client.setex(cache_key, ttl, serialized)
            return True
        except Exception as e:
            logger.error(f"Error caching tool result: {e}")
            return False

    def get_cached_tool_result(self, tool_name: str, params: Dict[str, Any]) -> Optional[Any]:
        """Retrieves a cached tool result if present."""
        param_hash = self.hash_params(params)
        cache_key = f"cache:tool:{tool_name}:{param_hash}"
        try:
            val = self.client.get(cache_key)
            if val is not None:
                return json.loads(val)
            return None
        except Exception as e:
            logger.error(f"Error reading tool cache: {e}")
            return None

    def clear_all_cache(self) -> bool:
        """Clears all keys in cache/memory (useful for clean test resets)."""
        try:
            if hasattr(self.client, "flushdb"):
                self.client.flushdb()
            if hasattr(self.client, "_store"):
                self.client._store.clear()
            if hasattr(self.client, "_hashes"):
                self.client._hashes.clear()
            return True
        except Exception as e:
            logger.error(f"Error clearing all cache: {e}")
            return False


memory_manager = MemoryManager()
