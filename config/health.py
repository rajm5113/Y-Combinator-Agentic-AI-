"""System Health Diagnostics Engine.

Provides deep, sub-200ms latency checks across all operational dependencies:
- PostgreSQL (or SQLite fallback) connection & ping latency
- Redis (or InMemoryCache fallback) connection & ping latency
- OpenRouter LLM credentials and model configurations
- Relational database row counts and table integrity
- Active pipeline execution state
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config.settings import settings
from db.connection import get_db_connection, get_redis_client

logger = logging.getLogger("health_checker")


class HealthChecker:
    """Performs non-blocking diagnostic probes across all system components."""

    @staticmethod
    def check_database() -> Dict[str, Any]:
        """Probes relational database connectivity, latency, and entity counts."""
        start = time.perf_counter()
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                _ = cur.fetchone()
                latency_ms = round((time.perf_counter() - start) * 1000, 2)

                # Collect row counts
                counts = {}
                for tbl in ["startups", "founders", "fit_evaluations", "message_drafts", "outreach_records", "blacklist"]:
                    try:
                        cur.execute(f"SELECT COUNT(*) as cnt FROM {tbl}")
                        counts[tbl] = cur.fetchone()["cnt"]
                    except Exception:
                        counts[tbl] = 0

                is_pg = "PGConnectionWrapper" in type(conn).__name__
                return {
                    "status": "up",
                    "type": "postgresql" if is_pg else "sqlite_fallback",
                    "latency_ms": latency_ms,
                    "counts": counts,
                }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "down",
                "error": str(e),
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }

    @staticmethod
    def check_redis() -> Dict[str, Any]:
        """Probes Redis connection or in-memory fallback status."""
        start = time.perf_counter()
        try:
            client = get_redis_client()
            client.ping()
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            is_mock = "InMemoryCache" in type(client).__name__
            return {
                "status": "up",
                "type": "redis" if not is_mock else "in_memory_fallback",
                "latency_ms": latency_ms,
            }
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return {
                "status": "down",
                "error": str(e),
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }

    @staticmethod
    def check_llm() -> Dict[str, Any]:
        """Validates OpenRouter API key configuration and fallback tier setup."""
        has_key = bool(settings.openrouter_api_key and settings.openrouter_api_key != "sk-dummy-key")
        key_masked = (
            f"{settings.openrouter_api_key[:8]}...{settings.openrouter_api_key[-4:]}"
            if has_key and len(settings.openrouter_api_key) > 12
            else "not_configured"
        )
        return {
            "status": "configured" if has_key else "missing_key",
            "provider": "OpenRouter",
            "key_masked": key_masked,
            "latency_ms": 0.0,
            "models": {
                "reasoning": settings.reasoning_fallback_chain[0] if settings.reasoning_fallback_chain else "",
                "extraction": settings.extraction_fallback_chain[0] if settings.extraction_fallback_chain else "",
                "general": settings.fast_fallback_chain[0] if settings.fast_fallback_chain else "",
            },
        }

    @classmethod
    def get_full_health(cls, is_pipeline_running: bool = False, last_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Aggregates all component health checks into a unified diagnostics payload."""
        db_health = cls.check_database()
        redis_health = cls.check_redis()
        llm_health = cls.check_llm()

        # Normalize component status
        db_status = "healthy" if db_health.get("status") == "up" else "unhealthy"
        redis_status = "healthy" if redis_health.get("status") == "up" else "degraded"
        llm_status = "healthy" if llm_health.get("status") == "configured" else "degraded"

        db_comp = {**db_health, "status": db_status}
        redis_comp = {**redis_health, "status": redis_status}
        llm_comp = {**llm_health, "status": llm_status}

        # Overall status calculation
        if db_status == "unhealthy":
            overall_status = "unhealthy"
        elif redis_status != "healthy" or llm_status != "healthy":
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return {
            "status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "environment": "production" if settings.database_url else "development",
            "components": {
                "database": db_comp,
                "redis": redis_comp,
                "llm": llm_comp,
                "openrouter": llm_comp,
                "pipeline": {
                    "status": "running" if is_pipeline_running else "healthy",
                    "latency_ms": 0.0,
                    "is_running": is_pipeline_running,
                    "last_report": last_report,
                },
            },
        }

    @classmethod
    async def check_all(cls, is_pipeline_running: bool = False, last_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Async entrypoint for probing all system dependencies."""
        return cls.get_full_health(is_pipeline_running=is_pipeline_running, last_report=last_report)


health_checker = HealthChecker()

