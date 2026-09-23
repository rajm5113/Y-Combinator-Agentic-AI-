"""Database Backup, Snapshot & Restore Engine.

Enables 1-click snapshot creation, listing, and atomic restoration
of the entire outreach database (startups, founders, fit evaluations,
message drafts, outreach records, blacklist, and pipeline runs).
"""

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from db.connection import get_db_connection

logger = logging.getLogger("backup_engine")

BACKUP_DIR = Path("data/backups")
TABLES_IN_ORDER = [
    "startups",
    "founders",
    "fit_evaluations",
    "message_drafts",
    "outreach_records",
    "blacklist",
    "pipeline_runs",
]


class BackupEngine:
    """Manages full database snapshotting and restoration."""

    def __init__(self, backup_dir: Path = BACKUP_DIR):
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Creates a timestamped, gzip-compressed JSON snapshot of all tables."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if output_path:
            target_file = Path(output_path)
            target_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            target_file = self.backup_dir / f"outreach_snapshot_{timestamp}.json.gz"

        now_iso = datetime.now(timezone.utc).isoformat()
        snapshot_data = {
            "version": "1.0",
            "created_at": now_iso,
            "metadata": {
                "version": "1.0",
                "created_at": now_iso,
            },
            "tables": {},
            "counts": {},
        }

        with get_db_connection() as conn:
            cur = conn.cursor()
            for table in TABLES_IN_ORDER:
                try:
                    cur.execute(f"SELECT * FROM {table}")
                    rows = cur.fetchall()
                    serialized_rows = []
                    for row in rows:
                        row_dict = dict(row)
                        # Normalize datetimes to ISO strings
                        for k, v in row_dict.items():
                            if isinstance(v, datetime):
                                row_dict[k] = v.isoformat()
                        serialized_rows.append(row_dict)
                    snapshot_data["tables"][table] = serialized_rows
                    snapshot_data["counts"][table] = len(serialized_rows)
                except Exception as e:
                    logger.warning(f"Table {table} could not be backed up ({e}); writing empty list.")
                    snapshot_data["tables"][table] = []
                    snapshot_data["counts"][table] = 0

        # Write compressed or plain json based on suffix
        json_bytes = json.dumps(snapshot_data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        if str(target_file).endswith(".gz"):
            with gzip.open(target_file, "wb") as f:
                f.write(json_bytes)
        else:
            with open(target_file, "wb") as f:
                f.write(json_bytes)

        file_size = target_file.stat().st_size
        logger.info(f"✓ Created backup snapshot: {target_file} ({file_size} bytes)")

        return {
            "success": True,
            "filename": target_file.name,
            "filepath": str(target_file.resolve()),
            "size_bytes": file_size,
            "file_size_bytes": file_size,
            "created_at": snapshot_data["created_at"],
            "table_counts": snapshot_data["counts"],
            "counts": snapshot_data["counts"],
        }

    def list_backups(self) -> List[Dict[str, Any]]:
        """Returns all available backups sorted by modification time DESC."""
        backups = []
        for file in sorted(self.backup_dir.glob("*.json*"), key=lambda f: f.stat().st_mtime, reverse=True):
            stat = file.stat()
            counts = {}
            try:
                if file.name.endswith(".gz"):
                    with gzip.open(file, "rt", encoding="utf-8") as f:
                        meta = json.load(f)
                        counts = meta.get("counts", {})
                else:
                    with open(file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        counts = meta.get("counts", {})
            except Exception:
                pass

            backups.append({
                "filename": file.name,
                "filepath": str(file.resolve()),
                "size_bytes": stat.st_size,
                "file_size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "table_counts": counts,
            })
        return backups

    def restore_backup(self, filepath: str) -> Dict[str, Any]:
        """Restores all tables from a snapshot file inside an atomic transaction."""
        requested = Path(filepath)
        # Restore is intentionally limited to snapshots inside backup_dir.
        # This prevents path traversal and arbitrary local file reads.
        if requested.is_absolute() or requested.name != filepath or requested.name in (".", ".."):
            raise ValueError("Invalid backup filename")
        file_path = (self.backup_dir / requested.name).resolve()
        backup_root = self.backup_dir.resolve()
        if file_path.parent != backup_root or not file_path.exists():
            raise FileNotFoundError(f"Backup file not found: {requested.name}")

        # Read compressed or uncompressed
        if str(file_path).endswith(".gz"):
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

        tables = data.get("tables", {})
        restored_counts = {}

        with get_db_connection() as conn:
            cur = conn.cursor()

            # Clean existing tables in reverse dependency order
            for table in reversed(TABLES_IN_ORDER):
                try:
                    cur.execute(f"DELETE FROM {table}")
                except Exception as e:
                    logger.warning(f"Could not clear table {table} during restore: {e}")

            # Re-insert in forward order
            for table in TABLES_IN_ORDER:
                rows = tables.get(table, [])
                restored_counts[table] = 0
                if not rows:
                    continue

                for row in rows:
                    cols = list(row.keys())
                    placeholders = ", ".join("?" for _ in cols)
                    col_names = ", ".join(cols)
                    query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
                    params = [row[c] for c in cols]
                    cur.execute(query, params)
                    restored_counts[table] += 1

        logger.info(f"✓ Restored database from {file_path.name}: {restored_counts}")
        return {
            "success": True,
            "filename": file_path.name,
            "restored_at": datetime.now(timezone.utc).isoformat(),
            "table_counts": restored_counts,
            "restored_counts": restored_counts,
            "counts": restored_counts,
        }


backup_engine = BackupEngine()
