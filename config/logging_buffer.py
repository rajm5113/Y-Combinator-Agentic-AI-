"""In-Memory Ring-Buffered Log Handler for Live Observability.

Captures recent structured log records in a circular deque (maxlen=1000)
to support live log streaming in the dashboard UI and API log querying.
"""

import collections
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class RingBufferLogHandler(logging.Handler):
    """Logging handler storing the most recent log entries in an in-memory deque."""

    def __init__(self, capacity: int = 1000, maxlen: Optional[int] = None):
        super().__init__()
        cap = maxlen if maxlen is not None else capacity
        self.capacity = cap
        self.buffer = collections.deque(maxlen=cap)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            self.buffer.append(entry)
        except Exception:
            self.handleError(record)

    def get_logs(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Returns buffered log entries with optional level and timestamp filtering."""
        filtered = list(self.buffer)

        if level:
            target_level = level.upper()
            filtered = [e for e in filtered if e["level"] == target_level]

        if since:
            filtered = [e for e in filtered if e["timestamp"] > since]

        if limit and limit > 0:
            filtered = filtered[-limit:]

        return filtered

    def clear(self) -> None:
        """Clears the ring buffer."""
        self.buffer.clear()


# Global ring buffer singleton
ring_buffer_handler = RingBufferLogHandler(capacity=1000)
ring_buffer_handler.setFormatter(logging.Formatter("%(message)s"))
global_log_buffer = ring_buffer_handler

# Automatically attach to root logger
root_logger = logging.getLogger()
if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
    root_logger.setLevel(logging.INFO)
if ring_buffer_handler not in root_logger.handlers:
    root_logger.addHandler(ring_buffer_handler)


