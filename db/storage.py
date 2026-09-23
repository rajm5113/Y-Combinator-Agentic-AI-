"""Relational Business Memory Engine & 'Never Contact Again' Blacklist Store.

Provides typed schema initialization, deduplication operations, and proactive
blacklist verification to prevent duplicate or embarrassing outreach.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from db.connection import get_db_connection
from db.models import (
    BlacklistEntry,
    FitEvaluation,
    Founder,
    FounderCreate,
    MessageDrafts,
    OutreachRecord,
    Startup,
    StartupCreate,
)

logger = logging.getLogger("storage_engine")


class StorageEngine:
    """Relational storage engine managing business memory and persistent state."""

    @staticmethod
    def init_db():
        """Initializes all required relational tables and indexes."""
        with get_db_connection() as conn:
            import sqlite3
            if isinstance(conn, sqlite3.Connection):
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS startups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    batch TEXT NOT NULL,
                    website TEXT,
                    one_liner TEXT,
                    long_description TEXT,
                    team_size INTEGER,
                    industry TEXT,
                    subindustry TEXT,
                    tags TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    is_hiring INTEGER DEFAULT 0,
                    yc_url TEXT,
                    jobs_data TEXT DEFAULT '[]',
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS founders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    full_name TEXT NOT NULL,
                    title TEXT,
                    bio TEXT,
                    linkedin_url TEXT,
                    twitter_url TEXT,
                    has_email INTEGER DEFAULT 0,
                    email TEXT,
                    avatar_url TEXT,
                    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (startup_id, full_name)
                );

                CREATE TABLE IF NOT EXISTS fit_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    startup_id INTEGER UNIQUE NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    score INTEGER NOT NULL CHECK (score >= 0 AND score <= 100),
                    fit_tier TEXT NOT NULL,
                    should_contact INTEGER NOT NULL DEFAULT 0,
                    match_rationale TEXT NOT NULL,
                    contribution_angle TEXT,
                    model_used TEXT,
                    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS message_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    founder_id INTEGER NOT NULL REFERENCES founders(id) ON DELETE CASCADE,
                    linkedin_note TEXT,
                    yc_job_note TEXT,
                    cold_email_subject TEXT,
                    cold_email_body TEXT,
                    model_used TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (startup_id, founder_id)
                );

                CREATE TABLE IF NOT EXISTS outreach_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    founder_id INTEGER NOT NULL REFERENCES founders(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'draft',
                    active_channel TEXT DEFAULT 'linkedin',
                    selected_message TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (startup_id, founder_id)
                );

                CREATE TABLE IF NOT EXISTS blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identifier_type TEXT NOT NULL,
                    identifier_value TEXT UNIQUE NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS implicit_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    insight_key TEXT NOT NULL,
                    insight_value TEXT NOT NULL,
                    confidence_score REAL DEFAULT 1.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (entity_type, entity_key, insight_key)
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    batch TEXT NOT NULL,
                    industry TEXT,
                    startup_limit INTEGER NOT NULL DEFAULT 5,
                    min_fit_score INTEGER NOT NULL DEFAULT 50,
                    max_concurrency INTEGER NOT NULL DEFAULT 5,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'running',
                    progress_message TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    duration_seconds REAL,
                    stats_json TEXT DEFAULT '{}',
                    error_summary TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_startups_slug ON startups(slug);
                CREATE INDEX IF NOT EXISTS idx_startups_batch ON startups(batch);
                CREATE INDEX IF NOT EXISTS idx_founders_name ON founders(full_name);
                CREATE INDEX IF NOT EXISTS idx_founders_linkedin ON founders(linkedin_url);
                CREATE INDEX IF NOT EXISTS idx_blacklist_value ON blacklist(identifier_value);
                CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_records(status);
                CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at);
                """)
                try:
                    conn.execute("ALTER TABLE startups ADD COLUMN jobs_data TEXT DEFAULT '[]'")
                except Exception:
                    pass
            else:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS startups (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    batch TEXT NOT NULL,
                    website TEXT,
                    one_liner TEXT,
                    long_description TEXT,
                    team_size INTEGER,
                    industry TEXT,
                    subindustry TEXT,
                    tags TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    is_hiring INTEGER DEFAULT 0,
                    yc_url TEXT,
                    jobs_data TEXT DEFAULT '[]',
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS founders (
                    id SERIAL PRIMARY KEY,
                    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    full_name TEXT NOT NULL,
                    title TEXT,
                    bio TEXT,
                    linkedin_url TEXT,
                    twitter_url TEXT,
                    has_email INTEGER DEFAULT 0,
                    email TEXT,
                    avatar_url TEXT,
                    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_startup_founder UNIQUE (startup_id, full_name)
                );

                CREATE TABLE IF NOT EXISTS fit_evaluations (
                    id SERIAL PRIMARY KEY,
                    startup_id INTEGER UNIQUE NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    score INTEGER NOT NULL CHECK (score >= 0 AND score <= 100),
                    fit_tier TEXT NOT NULL,
                    should_contact INTEGER NOT NULL DEFAULT 0,
                    match_rationale TEXT NOT NULL,
                    contribution_angle TEXT,
                    model_used TEXT,
                    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS message_drafts (
                    id SERIAL PRIMARY KEY,
                    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    founder_id INTEGER NOT NULL REFERENCES founders(id) ON DELETE CASCADE,
                    linkedin_note TEXT,
                    yc_job_note TEXT,
                    cold_email_subject TEXT,
                    cold_email_body TEXT,
                    model_used TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_message_draft UNIQUE (startup_id, founder_id)
                );

                CREATE TABLE IF NOT EXISTS outreach_records (
                    id SERIAL PRIMARY KEY,
                    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
                    founder_id INTEGER NOT NULL REFERENCES founders(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'draft',
                    active_channel TEXT DEFAULT 'linkedin',
                    selected_message TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_outreach_record UNIQUE (startup_id, founder_id)
                );

                CREATE TABLE IF NOT EXISTS blacklist (
                    id SERIAL PRIMARY KEY,
                    identifier_type TEXT NOT NULL,
                    identifier_value TEXT UNIQUE NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS implicit_insights (
                    id SERIAL PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    insight_key TEXT NOT NULL,
                    insight_value TEXT NOT NULL,
                    confidence_score REAL DEFAULT 1.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_implicit_insight UNIQUE (entity_type, entity_key, insight_key)
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(64) UNIQUE NOT NULL,
                    batch VARCHAR(64) NOT NULL,
                    industry VARCHAR(128),
                    startup_limit INTEGER NOT NULL DEFAULT 5,
                    min_fit_score INTEGER NOT NULL DEFAULT 50,
                    max_concurrency INTEGER NOT NULL DEFAULT 5,
                    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
                    status VARCHAR(32) NOT NULL DEFAULT 'running',
                    progress_message TEXT,
                    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP WITH TIME ZONE,
                    duration_seconds REAL,
                    stats_json JSONB DEFAULT '{}'::jsonb,
                    error_summary TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_startups_slug ON startups(slug);
                CREATE INDEX IF NOT EXISTS idx_startups_batch ON startups(batch);
                CREATE INDEX IF NOT EXISTS idx_founders_name ON founders(full_name);
                CREATE INDEX IF NOT EXISTS idx_founders_linkedin ON founders(linkedin_url);
                CREATE INDEX IF NOT EXISTS idx_blacklist_value ON blacklist(identifier_value);
                CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_records(status);
                CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at);
                """)

    # --- Startup & Founder Operations ---

    @staticmethod
    def upsert_startup(startup: StartupCreate) -> int:
        """Inserts or updates a startup by slug."""
        tags_json = json.dumps(startup.tags)
        jobs_json = json.dumps(startup.jobs_data) if getattr(startup, "jobs_data", None) else "[]"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO startups (
                    name, slug, batch, website, one_liner, long_description,
                    team_size, industry, subindustry, tags, status, is_hiring, yc_url, jobs_data, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    batch=excluded.batch,
                    website=COALESCE(excluded.website, startups.website),
                    one_liner=COALESCE(excluded.one_liner, startups.one_liner),
                    long_description=COALESCE(excluded.long_description, startups.long_description),
                    team_size=COALESCE(excluded.team_size, startups.team_size),
                    industry=COALESCE(excluded.industry, startups.industry),
                    subindustry=COALESCE(excluded.subindustry, startups.subindustry),
                    tags=excluded.tags,
                    status=excluded.status,
                    is_hiring=excluded.is_hiring,
                    yc_url=COALESCE(excluded.yc_url, startups.yc_url),
                    jobs_data=CASE WHEN excluded.jobs_data != '[]' THEN excluded.jobs_data ELSE startups.jobs_data END,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                startup.name, startup.slug, startup.batch, startup.website,
                startup.one_liner, startup.long_description, startup.team_size,
                startup.industry, startup.subindustry, tags_json, startup.status,
                1 if startup.is_hiring else 0, startup.yc_url, jobs_json
            ))
            cursor.execute("SELECT id FROM startups WHERE slug = ?", (startup.slug,))
            row = cursor.fetchone()
            return row["id"]

    @staticmethod
    def get_startup_by_slug(slug: str) -> Optional[Dict[str, Any]]:
        """Finds a startup by its slug."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM startups WHERE slug = ?", (slug,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("tags"):
                try:
                    item["tags"] = json.loads(item["tags"])
                except Exception:
                    item["tags"] = []
            if item.get("jobs_data"):
                try:
                    item["jobs_data"] = json.loads(item["jobs_data"])
                except Exception:
                    item["jobs_data"] = []
            return item

    @staticmethod
    def get_startups_without_founders() -> List[Dict[str, Any]]:
        """Returns startups that have no founders recorded yet."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.id, s.name, s.slug, s.batch, s.website, s.one_liner, s.yc_url, s.jobs_data, s.is_hiring
                FROM startups s
                LEFT JOIN founders f ON s.id = f.startup_id
                WHERE f.id IS NULL
                ORDER BY s.id ASC
            """)
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_founders_by_startup_id(startup_id: int) -> List[Dict[str, Any]]:
        """Retrieves all founders linked to a startup."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM founders WHERE startup_id = ? ORDER BY id ASC", (startup_id,))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def upsert_founder(founder: FounderCreate) -> int:
        """Inserts or updates a founder by startup_id + full_name."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO founders (
                    startup_id, full_name, title, bio, linkedin_url, twitter_url,
                    has_email, email, avatar_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(startup_id, full_name) DO UPDATE SET
                    title=COALESCE(excluded.title, founders.title),
                    bio=COALESCE(excluded.bio, founders.bio),
                    linkedin_url=COALESCE(excluded.linkedin_url, founders.linkedin_url),
                    twitter_url=COALESCE(excluded.twitter_url, founders.twitter_url),
                    has_email=excluded.has_email,
                    email=COALESCE(excluded.email, founders.email),
                    avatar_url=COALESCE(excluded.avatar_url, founders.avatar_url)
            """, (
                founder.startup_id, founder.full_name, founder.title, founder.bio,
                founder.linkedin_url, founder.twitter_url, 1 if founder.has_email else 0,
                founder.email, founder.avatar_url
            ))
            cursor.execute(
                "SELECT id FROM founders WHERE startup_id = ? AND full_name = ?",
                (founder.startup_id, founder.full_name)
            )
            row = cursor.fetchone()
            return row["id"]

    # --- Fit & Message Operations ---

    @staticmethod
    def save_fit_evaluation(fit: FitEvaluation) -> int:
        """Saves or updates a fit evaluation."""
        rationale_json = json.dumps(fit.match_rationale)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO fit_evaluations (
                    startup_id, score, fit_tier, should_contact,
                    match_rationale, contribution_angle, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(startup_id) DO UPDATE SET
                    score=excluded.score,
                    fit_tier=excluded.fit_tier,
                    should_contact=excluded.should_contact,
                    match_rationale=excluded.match_rationale,
                    contribution_angle=excluded.contribution_angle,
                    model_used=excluded.model_used,
                    evaluated_at=CURRENT_TIMESTAMP
            """, (
                fit.startup_id, fit.score, fit.fit_tier, 1 if fit.should_contact else 0,
                rationale_json, fit.contribution_angle, fit.model_used
            ))
            cursor.execute("SELECT id FROM fit_evaluations WHERE startup_id = ?", (fit.startup_id,))
            row = cursor.fetchone()
            return row["id"]

    @staticmethod
    def get_fit_evaluation_by_startup_id(startup_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves the fit evaluation for a startup."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM fit_evaluations WHERE startup_id = ?", (startup_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("match_rationale"):
                try:
                    item["match_rationale"] = json.loads(item["match_rationale"])
                except Exception:
                    item["match_rationale"] = []
            item["should_contact"] = bool(item.get("should_contact"))
            return item

    @staticmethod
    def get_startups_without_fit_evaluation() -> List[Dict[str, Any]]:
        """Returns startups that have no fit evaluation recorded yet."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.*
                FROM startups s
                LEFT JOIN fit_evaluations e ON s.id = e.startup_id
                WHERE e.id IS NULL
                ORDER BY s.id ASC
            """)
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                if item.get("tags"):
                    try:
                        item["tags"] = json.loads(item["tags"])
                    except Exception:
                        item["tags"] = []
                if item.get("jobs_data"):
                    try:
                        item["jobs_data"] = json.loads(item["jobs_data"])
                    except Exception:
                        item["jobs_data"] = []
                results.append(item)
            return results

    @staticmethod
    def get_startup_by_id(startup_id: int) -> Optional[Dict[str, Any]]:
        """Finds a startup by its primary key ID."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM startups WHERE id = ?", (startup_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("tags"):
                try:
                    item["tags"] = json.loads(item["tags"])
                except Exception:
                    item["tags"] = []
            if item.get("jobs_data"):
                try:
                    item["jobs_data"] = json.loads(item["jobs_data"])
                except Exception:
                    item["jobs_data"] = []
            return item

    @staticmethod
    def ensure_outreach_record(startup_id: int, founder_id: int) -> int:
        """Creates a discovery-stage outreach record for every founder exactly once."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO outreach_records (
                    startup_id, founder_id, status, active_channel, selected_message
                ) VALUES (?, ?, 'discovered', 'linkedin', NULL)
                ON CONFLICT(startup_id, founder_id) DO NOTHING
            """, (startup_id, founder_id))
            cursor.execute(
                "SELECT id FROM outreach_records WHERE startup_id = ? AND founder_id = ?",
                (startup_id, founder_id),
            )
            row = cursor.fetchone()
            return row["id"] if row else 0

    @staticmethod
    def ensure_outreach_records_for_founders() -> int:
        """Backfills discovery-stage outreach records for founders already in storage."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO outreach_records (
                    startup_id, founder_id, status, active_channel, selected_message
                )
                SELECT f.startup_id, f.id, 'discovered', 'linkedin', NULL
                FROM founders f
                LEFT JOIN outreach_records r
                    ON r.startup_id = f.startup_id AND r.founder_id = f.id
                WHERE r.id IS NULL
            """)
            return cursor.rowcount

    @staticmethod
    def save_message_drafts(drafts: MessageDrafts) -> int:
        """Saves message drafts and initializes the corresponding outreach record."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO message_drafts (
                    startup_id, founder_id, linkedin_note, yc_job_note,
                    cold_email_subject, cold_email_body, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(startup_id, founder_id) DO UPDATE SET
                    linkedin_note=excluded.linkedin_note,
                    yc_job_note=excluded.yc_job_note,
                    cold_email_subject=excluded.cold_email_subject,
                    cold_email_body=excluded.cold_email_body,
                    model_used=excluded.model_used
            """, (
                drafts.startup_id, drafts.founder_id, drafts.linkedin_note,
                drafts.yc_job_note, drafts.cold_email_subject, drafts.cold_email_body,
                drafts.model_used
            ))
            cursor.execute(
                "SELECT id FROM message_drafts WHERE startup_id = ? AND founder_id = ?",
                (drafts.startup_id, drafts.founder_id)
            )
            draft_id = cursor.fetchone()["id"]

            # Initialize or promote the outreach record.
            # Discovery-stage records become reviewable drafts once messages exist.
            cursor.execute("""
                INSERT INTO outreach_records (startup_id, founder_id, status, active_channel, selected_message)
                VALUES (?, ?, 'draft', 'linkedin', ?)
                ON CONFLICT(startup_id, founder_id) DO NOTHING
            """, (drafts.startup_id, drafts.founder_id, drafts.linkedin_note))
            cursor.execute("""
                UPDATE outreach_records
                SET status = 'draft',
                    active_channel = 'linkedin',
                    selected_message = COALESCE(?, selected_message),
                    updated_at = CURRENT_TIMESTAMP
                WHERE startup_id = ? AND founder_id = ? AND status = 'discovered'
            """, (drafts.linkedin_note, drafts.startup_id, drafts.founder_id))

            return draft_id

    # --- Blacklist / Memory Engine ---

    @staticmethod
    def is_blacklisted(
        founder_name: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        company_name: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> bool:
        """Memory verification check: returns True if ANY identifier matches the blacklist."""
        identifiers = []
        if founder_name:
            identifiers.append(founder_name.strip().lower())
        if linkedin_url:
            identifiers.append(linkedin_url.strip().lower())
        if company_name:
            identifiers.append(company_name.strip().lower())
        if domain:
            identifiers.append(domain.strip().lower())

        if not identifiers:
            return False

        placeholders = ",".join("?" for _ in identifiers)
        query = f"""
            SELECT id FROM blacklist 
            WHERE LOWER(identifier_value) IN ({placeholders})
            LIMIT 1
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, identifiers)
            return cursor.fetchone() is not None

    @staticmethod
    def add_to_blacklist(identifier_type: str, identifier_value: str, reason: str) -> int:
        """Permanently blacklists an identifier and updates matching outreach records."""
        clean_value = identifier_value.strip()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO blacklist (identifier_type, identifier_value, reason)
                VALUES (?, ?, ?)
                ON CONFLICT(identifier_value) DO UPDATE SET
                    reason=excluded.reason
            """, (identifier_type, clean_value, reason))
            cursor.execute("SELECT id FROM blacklist WHERE identifier_value = ?", (clean_value,))
            entry_id = cursor.fetchone()["id"]

            # Cascade to active outreach records
            if identifier_type == "company_name":
                cursor.execute("""
                    UPDATE outreach_records SET status = 'blacklisted', updated_at = CURRENT_TIMESTAMP
                    WHERE startup_id IN (SELECT id FROM startups WHERE LOWER(name) = LOWER(?))
                """, (clean_value,))
            elif identifier_type in ("founder_name", "linkedin_url"):
                cursor.execute("""
                    UPDATE outreach_records SET status = 'blacklisted', updated_at = CURRENT_TIMESTAMP
                    WHERE founder_id IN (
                        SELECT id FROM founders 
                        WHERE LOWER(full_name) = LOWER(?) OR LOWER(linkedin_url) = LOWER(?)
                    )
                """, (clean_value, clean_value))

            return entry_id

    # --- Dashboard & Review Queries ---

    @staticmethod
    def get_dashboard_stats() -> Dict[str, int]:
        """Returns aggregate metric counts for UI cards."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM startups")
            total_startups = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations WHERE fit_tier = 'HIGH'")
            high_fit = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations WHERE fit_tier = 'MEDIUM'")
            medium_fit = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status = 'draft'")
            pending_review = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status = 'sent'")
            contacted = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM blacklist")
            blacklisted = cursor.fetchone()["cnt"]

            return {
                "total_startups": total_startups,
                "high_fit": high_fit,
                "medium_fit": medium_fit,
                "pending_review": pending_review,
                "contacted": contacted,
                "blacklisted": blacklisted,
            }

    @staticmethod
    def get_review_leads(status_filter: Optional[str] = None, fit_tier_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Multi-table JOIN query retrieving enriched lead dossiers for human review."""
        query = """
            SELECT 
                r.id as outreach_id,
                r.status as outreach_status,
                r.active_channel,
                r.selected_message,
                r.notes,
                s.id as startup_id,
                s.name as company_name,
                s.slug as company_slug,
                s.batch as company_batch,
                s.website as company_website,
                s.one_liner,
                s.long_description,
                s.industry,
                s.tags,
                f.id as founder_id,
                f.full_name as founder_name,
                f.title as founder_title,
                f.bio as founder_bio,
                f.linkedin_url as founder_linkedin,
                e.score as fit_score,
                e.fit_tier,
                e.match_rationale,
                e.contribution_angle,
                d.linkedin_note,
                d.yc_job_note,
                d.cold_email_subject,
                d.cold_email_body
            FROM outreach_records r
            JOIN startups s ON r.startup_id = s.id
            JOIN founders f ON r.founder_id = f.id
            LEFT JOIN fit_evaluations e ON s.id = e.startup_id
            LEFT JOIN message_drafts d ON s.id = d.startup_id AND f.id = d.founder_id
            WHERE 1=1
        """
        params = []
        if status_filter:
            query += " AND r.status = ?"
            params.append(status_filter)
        if fit_tier_filter:
            query += " AND e.fit_tier = ?"
            params.append(fit_tier_filter.upper())

        query += " ORDER BY COALESCE(e.score, 0) DESC, r.created_at DESC"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                if item.get("tags"):
                    try:
                        item["tags"] = json.loads(item["tags"])
                    except Exception:
                        item["tags"] = []
                if item.get("match_rationale"):
                    try:
                        item["match_rationale"] = json.loads(item["match_rationale"])
                    except Exception:
                        item["match_rationale"] = []
                results.append(item)
            return results

    @staticmethod
    def update_outreach_status(
        record_id: int,
        status: str,
        selected_message: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Updates outreach status, edited message, and human feedback notes."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE outreach_records SET
                    status = ?,
                    selected_message = COALESCE(?, selected_message),
                    notes = COALESCE(?, notes),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, selected_message, notes, record_id))
            return cursor.rowcount > 0

    # --- Phase 4 Orchestration & CLI Helpers ---

    @staticmethod
    def get_all_startups() -> List[Dict[str, Any]]:
        """Returns all startups stored in the database."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM startups ORDER BY id ASC")
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                if item.get("tags"):
                    try:
                        item["tags"] = json.loads(item["tags"])
                    except Exception:
                        item["tags"] = []
                if item.get("jobs_data"):
                    try:
                        item["jobs_data"] = json.loads(item["jobs_data"])
                    except Exception:
                        item["jobs_data"] = []
                results.append(item)
            return results

    @staticmethod
    def get_startups_needing_founders() -> List[Dict[str, Any]]:
        """Returns startups that have zero founders extracted yet."""
        return StorageEngine.get_startups_without_founders()

    @staticmethod
    def get_qualified_startup_ids(min_score: int = 50) -> List[int]:
        """Returns startup IDs with fit score >= threshold that lack message drafts."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT e.startup_id, e.score
                FROM fit_evaluations e
                LEFT JOIN message_drafts d ON e.startup_id = d.startup_id
                WHERE e.score >= ? AND d.id IS NULL
                ORDER BY e.score DESC
            """, (min_score,))
            return [row["startup_id"] for row in cursor.fetchall()]

    @staticmethod
    def get_pipeline_stats() -> Dict[str, Any]:
        """Returns aggregate metrics for CLI stats command."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM startups")
            startups_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM founders")
            founders_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations")
            evals_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations WHERE fit_tier = 'HIGH'")
            high_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations WHERE fit_tier = 'MEDIUM'")
            med_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations WHERE fit_tier = 'LOW'")
            low_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM message_drafts")
            drafts_cnt = cursor.fetchone()["cnt"]

            cursor.execute("SELECT status, COUNT(*) as cnt FROM outreach_records GROUP BY status")
            outreach_counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) as cnt FROM blacklist")
            blacklist_cnt = cursor.fetchone()["cnt"]

            return {
                "startups": startups_cnt,
                "founders": founders_cnt,
                "fit_evaluations": evals_cnt,
                "fit_high": high_cnt,
                "fit_medium": med_cnt,
                "fit_low": low_cnt,
                "message_drafts": drafts_cnt,
                "outreach_status": {
                    "draft": outreach_counts.get("draft", 0),
                    "approved": outreach_counts.get("approved", 0),
                    "sent": outreach_counts.get("sent", 0),
                    "replied": outreach_counts.get("replied", 0),
                    "rejected": outreach_counts.get("rejected", 0),
                    "blacklisted": outreach_counts.get("blacklisted", 0),
                },
                "blacklisted": blacklist_cnt,
            }

    @staticmethod
    def get_blacklist_entries() -> List[Dict[str, Any]]:
        """Returns all blacklist entries."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM blacklist ORDER BY id ASC")
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def remove_from_blacklist(identifier_value: str) -> bool:
        """Removes an identifier from the blacklist."""
        clean_value = identifier_value.strip()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blacklist WHERE LOWER(identifier_value) = LOWER(?)", (clean_value,))
            return cursor.rowcount > 0

    @staticmethod
    def get_qualified_leads_for_export(min_score: int = 50) -> List[Dict[str, Any]]:
        """Returns enriched qualified leads joined with founders and message drafts for CSV/JSON export."""
        query = """
            SELECT 
                s.name as startup_name,
                s.slug,
                s.batch,
                f.full_name as founder_name,
                f.title as founder_title,
                f.linkedin_url,
                COALESCE(e.score, 0) as fit_score,
                COALESCE(e.fit_tier, 'UNKNOWN') as fit_tier,
                COALESCE(e.contribution_angle, '') as contribution_angle,
                COALESCE(d.linkedin_note, '') as linkedin_note,
                COALESCE(d.yc_job_note, '') as yc_job_note,
                COALESCE(d.cold_email_subject, '') as cold_email_subject,
                COALESCE(d.cold_email_body, '') as cold_email_body,
                COALESCE(r.status, 'draft') as outreach_status
            FROM startups s
            JOIN fit_evaluations e ON s.id = e.startup_id
            JOIN founders f ON s.id = f.startup_id
            LEFT JOIN message_drafts d ON s.id = d.startup_id AND f.id = d.founder_id
            LEFT JOIN outreach_records r ON s.id = r.startup_id AND f.id = r.founder_id
            WHERE e.score >= ?
            ORDER BY e.score DESC, s.name ASC
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (min_score,))
            return [dict(row) for row in cursor.fetchall()]

    # --- Phase 5 Dashboard Queries ---

    @staticmethod
    def get_leads_paginated(
        status: Optional[str] = None,
        fit_tier: Optional[str] = None,
        batch: Optional[str] = None,
        search_query: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ):
        """Paginated lead listing with server-side filtering.

        Returns (leads_list, total_count) tuple.
        """
        base_query = """
            SELECT
                r.id as outreach_id,
                s.name as startup_name,
                s.slug,
                s.batch,
                f.full_name as founder_name,
                f.title as founder_title,
                f.linkedin_url,
                COALESCE(e.score, 0) as fit_score,
                COALESCE(e.fit_tier, 'UNKNOWN') as fit_tier,
                r.status as outreach_status,
                r.active_channel,
                CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END as has_drafts
            FROM outreach_records r
            JOIN startups s ON r.startup_id = s.id
            JOIN founders f ON r.founder_id = f.id
            LEFT JOIN fit_evaluations e ON s.id = e.startup_id
            LEFT JOIN message_drafts d ON s.id = d.startup_id AND f.id = d.founder_id
            WHERE 1=1
        """
        count_query = """
            SELECT COUNT(*) as cnt
            FROM outreach_records r
            JOIN startups s ON r.startup_id = s.id
            JOIN founders f ON r.founder_id = f.id
            LEFT JOIN fit_evaluations e ON s.id = e.startup_id
            WHERE 1=1
        """
        params = []
        count_params = []

        if status:
            if status in ("pending_review", "needs_review"):
                base_query += " AND r.status IN ('draft', 'review')"
                count_query += " AND r.status IN ('draft', 'review')"
            else:
                base_query += " AND r.status = ?"
                count_query += " AND r.status = ?"
                params.append(status)
                count_params.append(status)
        if fit_tier:
            base_query += " AND e.fit_tier = ?"
            count_query += " AND e.fit_tier = ?"
            params.append(fit_tier.upper())
            count_params.append(fit_tier.upper())
        if batch:
            base_query += " AND s.batch = ?"
            count_query += " AND s.batch = ?"
            params.append(batch)
            count_params.append(batch)
        if search_query:
            like_val = f"%{search_query}%"
            base_query += " AND (LOWER(s.name) LIKE LOWER(?) OR LOWER(f.full_name) LIKE LOWER(?))"
            count_query += " AND (LOWER(s.name) LIKE LOWER(?) OR LOWER(f.full_name) LIKE LOWER(?))"
            params.extend([like_val, like_val])
            count_params.extend([like_val, like_val])

        base_query += """ ORDER BY 
            CASE 
                WHEN r.status IN ('draft', 'review') THEN 0 
                WHEN r.status = 'approved' THEN 1 
                WHEN r.status = 'sent' THEN 2 
                WHEN r.status = 'replied' THEN 3 
                ELSE 4 
            END ASC,
            CASE 
                WHEN e.fit_tier = 'HIGH' THEN 0 
                WHEN e.fit_tier = 'MEDIUM' THEN 1 
                WHEN e.fit_tier = 'LOW' THEN 2 
                ELSE 3 
            END ASC,
            COALESCE(e.score, 0) DESC,
            r.created_at DESC
        """
        offset = (page - 1) * per_page
        base_query += f" LIMIT {per_page} OFFSET {offset}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(count_query, count_params)
            total = cursor.fetchone()["cnt"]

            cursor.execute(base_query, params)
            leads = []
            for row in cursor.fetchall():
                item = dict(row)
                item["has_drafts"] = bool(item.get("has_drafts"))
                leads.append(item)
            return leads, total

    @staticmethod
    def get_lead_detail(outreach_id: int) -> Optional[Dict[str, Any]]:
        """Full enriched lead dossier by outreach record ID."""
        query = """
            SELECT
                r.id as outreach_id,
                r.status as outreach_status,
                r.active_channel,
                r.selected_message,
                r.notes,
                s.id as startup_id,
                s.name as startup_name,
                s.slug,
                s.batch,
                s.website,
                s.one_liner,
                s.long_description,
                s.industry,
                s.tags,
                s.is_hiring,
                s.jobs_data,
                f.id as founder_id,
                f.full_name as founder_name,
                f.title as founder_title,
                f.bio as founder_bio,
                f.linkedin_url,
                f.twitter_url,
                COALESCE(e.score, 0) as fit_score,
                COALESCE(e.fit_tier, 'UNKNOWN') as fit_tier,
                e.match_rationale,
                e.contribution_angle,
                d.linkedin_note,
                d.yc_job_note,
                d.cold_email_subject,
                d.cold_email_body
            FROM outreach_records r
            JOIN startups s ON r.startup_id = s.id
            JOIN founders f ON r.founder_id = f.id
            LEFT JOIN fit_evaluations e ON s.id = e.startup_id
            LEFT JOIN message_drafts d ON s.id = d.startup_id AND f.id = d.founder_id
            WHERE r.id = ?
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (outreach_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            # Parse JSON fields
            for json_field in ("tags", "jobs_data", "match_rationale"):
                if item.get(json_field):
                    try:
                        item[json_field] = json.loads(item[json_field])
                    except Exception:
                        item[json_field] = []
                else:
                    item[json_field] = []
            item["is_hiring"] = bool(item.get("is_hiring"))
            return item

    @staticmethod
    def get_batch_list() -> List[Dict[str, Any]]:
        """Returns distinct batch names with startup counts."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT batch, COUNT(*) as count FROM startups GROUP BY batch ORDER BY batch DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update_message_draft(
        startup_id: int,
        founder_id: int,
        linkedin_note: Optional[str] = None,
        yc_job_note: Optional[str] = None,
        cold_email_subject: Optional[str] = None,
        cold_email_body: Optional[str] = None,
    ) -> bool:
        """Updates individual message draft fields (human edits from dashboard)."""
        updates = []
        params = []
        if linkedin_note is not None:
            updates.append("linkedin_note = ?")
            params.append(linkedin_note)
        if yc_job_note is not None:
            updates.append("yc_job_note = ?")
            params.append(yc_job_note)
        if cold_email_subject is not None:
            updates.append("cold_email_subject = ?")
            params.append(cold_email_subject)
        if cold_email_body is not None:
            updates.append("cold_email_body = ?")
            params.append(cold_email_body)

        if not updates:
            return False

        params.extend([startup_id, founder_id])
        query = f"UPDATE message_drafts SET {', '.join(updates)} WHERE startup_id = ? AND founder_id = ?"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.rowcount > 0

    @staticmethod
    def get_funnel_stats() -> Dict[str, Any]:
        """Returns extended conversion funnel metrics for the dashboard overview."""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) as cnt FROM startups")
            total_startups = cursor.fetchone()["cnt"]

            cursor.execute("""
                SELECT COUNT(DISTINCT s.id) as cnt FROM startups s
                JOIN founders f ON s.id = f.startup_id
            """)
            with_founders = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations")
            evaluated = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM fit_evaluations WHERE score >= 50")
            qualified = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM message_drafts")
            drafted = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status IN ('draft', 'review')")
            needs_review = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status = 'approved'")
            approved = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status = 'sent'")
            sent = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status = 'replied'")
            replied = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM outreach_records WHERE status = 'rejected'")
            rejected = cursor.fetchone()["cnt"]

            return {
                "total_startups": total_startups,
                "with_founders": with_founders,
                "evaluated": evaluated,
                "qualified": qualified,
                "drafted": drafted,
                "needs_review": needs_review,
                "approved": approved,
                "sent": sent,
                "replied": replied,
                "rejected": rejected,
            }

    # --- Phase 6 Pipeline Run Tracking & Operational Methods ---

    @staticmethod
    def record_pipeline_run_start(
        session_id: str,
        batch: str,
        industry: Optional[str] = None,
        startup_limit: int = 5,
        min_fit_score: int = 50,
        max_concurrency: int = 5,
        dry_run: bool = False,
    ) -> int:
        """Records the initialization of a pipeline run in the database."""
        query = """
            INSERT INTO pipeline_runs (
                session_id, batch, industry, startup_limit, min_fit_score,
                max_concurrency, dry_run, status, progress_message, started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 'Starting pipeline...', CURRENT_TIMESTAMP)
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                query,
                (
                    session_id,
                    batch,
                    industry,
                    startup_limit,
                    min_fit_score,
                    max_concurrency,
                    bool(dry_run),
                ),
            )
            cur.execute("SELECT id FROM pipeline_runs WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            return row["id"] if row else 0

    @staticmethod
    def update_pipeline_run_progress(
        session_id: str,
        progress_message: str,
        status: str = "running",
    ) -> bool:
        """Updates live progress text and status for a running pipeline."""
        query = """
            UPDATE pipeline_runs
            SET progress_message = ?, status = ?
            WHERE session_id = ?
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (progress_message, status, session_id))
            return cur.rowcount > 0

    @staticmethod
    def record_pipeline_run_complete(
        session_id: str,
        status: str = "completed",
        duration_seconds: float = 0.0,
        stats: Optional[Dict[str, Any]] = None,
        error_summary: Optional[str] = None,
    ) -> bool:
        """Finalizes a pipeline run record with duration, metrics, and completion status."""
        stats_json = json.dumps(stats or {})
        query = """
            UPDATE pipeline_runs
            SET status = ?,
                duration_seconds = ?,
                stats_json = ?,
                error_summary = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (status, duration_seconds, stats_json, error_summary, session_id))
            return cur.rowcount > 0

    @staticmethod
    def get_last_pipeline_run_config() -> Optional[Dict[str, Any]]:
        """Retrieves parameters of the most recent pipeline run for the 'Run Again' quick-trigger."""
        query = """
            SELECT batch, industry, startup_limit, min_fit_score, max_concurrency, dry_run, status, started_at
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT 1
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            res["dry_run"] = bool(res.get("dry_run"))
            if res.get("started_at"):
                res["started_at"] = str(res["started_at"])
            return res

    @staticmethod
    def get_pipeline_run_history(limit: int = 20) -> List[Dict[str, Any]]:
        """Returns recent pipeline execution history sorted by started_at DESC."""
        query = """
            SELECT id, session_id, batch, industry, startup_limit, min_fit_score,
                   max_concurrency, dry_run, status, progress_message,
                   started_at, completed_at, duration_seconds, stats_json, error_summary
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT ?
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            history = []
            for r in rows:
                item = dict(r)
                item["dry_run"] = bool(item.get("dry_run"))
                if item.get("stats_json") and isinstance(item["stats_json"], str):
                    try:
                        item["stats_json"] = json.loads(item["stats_json"])
                    except Exception:
                        pass
                for date_col in ["started_at", "completed_at"]:
                    if item.get(date_col):
                        item[date_col] = str(item[date_col])
                history.append(item)
            return history


storage_engine = StorageEngine()
