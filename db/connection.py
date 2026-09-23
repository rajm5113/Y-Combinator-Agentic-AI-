"""Database and cache connection manager.

Supports PostgreSQL and Redis in production with automatic, seamless
local SQLite and in-memory cache fallback for development and testing.
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional
from config.settings import settings

logger = logging.getLogger("db_connection")


class InMemoryCache:
    """Thread-safe in-memory cache emulator when Redis is not running."""

    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        self._store[key] = str(value)
        return True

    def setex(self, key: str, time: int, value: str) -> bool:
        self._store[key] = str(value)
        return True

    def expire(self, key: str, seconds: int) -> bool:
        # In-memory fallback is process-local; TTL is best-effort here.
        return key in self._store or key in self._hashes

    def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        if key in self._hashes:
            del self._hashes[key]
            return 1
        return 0

    def hset(self, name: str, key: str, value: Any) -> int:
        if name not in self._hashes:
            self._hashes[name] = {}
        self._hashes[name][key] = value
        return 1

    def hget(self, name: str, key: str) -> Optional[Any]:
        return self._hashes.get(name, {}).get(key)

    def hgetall(self, name: str) -> Dict[str, Any]:
        return dict(self._hashes.get(name, {}))

    def ping(self) -> bool:
        return True


def get_redis_client():
    """Initializes a Redis client or falls back to InMemoryCache."""
    try:
        import redis
        client = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3.0)
        client.ping()
        logger.info("Connected to live Redis server")
        return client
    except Exception as e:
        if settings.use_inmemory_cache_fallback:
            logger.info(f"Redis unavailable ({e}). Using InMemoryCache fallback.")
            return InMemoryCache()
        raise


class PGCursorWrapper:
    """Wraps a psycopg2 cursor to provide uniform row access and parameter translation."""

    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, query: str, params=None):
        pg_query = query.replace("?", "%s")
        if params is not None:
            return self._cursor.execute(pg_query, params)
        return self._cursor.execute(pg_query)

    def executemany(self, query: str, seq_of_params):
        pg_query = query.replace("?", "%s")
        return self._cursor.executemany(pg_query, seq_of_params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class PGConnectionWrapper:
    """Wraps a psycopg2 connection for uniform context management and cursor creation."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        import psycopg2.extras
        return PGCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def execute(self, query: str, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def executemany(self, query: str, seq_of_params):
        cur = self.cursor()
        cur.executemany(query, seq_of_params)
        return cur

    def executescript(self, script: str):
        with self._conn.cursor() as cur:
            cur.execute(script)
        self._conn.commit()


@contextmanager
def get_db_connection() -> Generator[Any, None, None]:
    """Provides a database connection.
    
    Prefers live PostgreSQL if configured and reachable; seamlessly falls back
    to SQLite if PostgreSQL is unavailable or in development mode.
    """
    conn = None
    if settings.database_url and (
        settings.database_url.startswith("postgresql") or settings.database_url.startswith("postgres")
    ):
        try:
            import psycopg2
            raw_conn = psycopg2.connect(settings.database_url, connect_timeout=3)
            conn = PGConnectionWrapper(raw_conn)
        except Exception as e:
            if not settings.use_sqlite_fallback:
                raise
            logger.warning(f"PostgreSQL connection failed ({e}). Falling back to SQLite.")

    if conn is None:
        settings.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        raw_sqlite = sqlite3.connect(str(settings.sqlite_db_path))
        raw_sqlite.row_factory = sqlite3.Row
        raw_sqlite.execute("PRAGMA foreign_keys = ON")
        conn = raw_sqlite

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
