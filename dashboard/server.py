"""FastAPI application for the YC Outreach Dashboard.

Provides REST endpoints for lead review, blacklist management,
pipeline triggering, and export — all reading from the same
StorageEngine + MemoryManager that the CLI uses.
"""

import asyncio
import os
import secrets
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config.health import health_checker
from config.logging_buffer import global_log_buffer
from config.settings import settings
from db.backup import backup_engine
from db.storage import storage_engine
from pipeline import MasterOrchestrator, PipelineConfig

from dashboard.api_models import (
    BackupCreateResponse,
    BackupItem,
    BackupRestoreRequest,
    BatchInfo,
    BlacklistAddRequest,
    BlacklistEntryResponse,
    ComponentHealth,
    FunnelStats,
    HealthResponse,
    LeadDetail,
    LeadListResponse,
    LeadSummary,
    LogEntry,
    LogsResponse,
    PipelineRunConfigResponse,
    PipelineRunHistoryItem,
    PipelineRunRequest,
    PipelineStatusResponse,
    RegenerateRequest,
    UpdateLeadRequest,
    UpdateLeadResponse,
)

logger = logging.getLogger("dashboard")

basic_auth = HTTPBasic(auto_error=False)

async def require_dashboard_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(basic_auth),
):
    """Protect API/state-changing endpoints when dashboard auth is enabled."""
    if not settings.dashboard_auth_enabled:
        return

    # Keep infrastructure probes and static assets publicly reachable.
    # The main dashboard page is protected so the browser can perform a
    # standard HTTP Basic Auth challenge before its JavaScript calls the API.
    public_paths = {"/health", "/api/health"}
    if request.url.path in public_paths or request.url.path.startswith("/static/"):
        return

    valid = (
        credentials is not None
        and secrets.compare_digest(credentials.username, settings.dashboard_auth_username)
        and secrets.compare_digest(credentials.password, settings.dashboard_auth_password)
        and bool(settings.dashboard_auth_username)
        and bool(settings.dashboard_auth_password)
    )
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )


# --- Background pipeline state (module-level, single-user tool) ---
_pipeline_lock = asyncio.Lock()
_current_orchestrator: Optional[MasterOrchestrator] = None
_pipeline_state: dict = {
    "is_running": False,
    "session_id": None,
    "started_at": None,
    "progress": None,
    "last_report": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB schema on startup."""
    storage_engine.init_db()
    # Backfill founder records into the discovery-stage lead queue so
    # discoveries are never stranded outside the review UI.
    try:
        storage_engine.ensure_outreach_records_for_founders()
    except Exception as e:
        logger.warning(f"Lead backfill skipped: {e}")
    logger.info(
        f"Dashboard ready at http://{settings.dashboard_host}:{settings.dashboard_port}"
    )
    yield


app = FastAPI(
    title="YC Outreach Dashboard",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_dashboard_auth)],
)

# Serve static frontend files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Root: Serve Dashboard SPA ─────────────────────────────────

@app.get("/")
async def serve_dashboard():
    """Serve the main dashboard page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(500, "Dashboard frontend not found")
    return FileResponse(str(index_path), media_type="text/html")


# ─── Stats ──────────────────────────────────────────────────────

@app.get("/api/stats", response_model=FunnelStats)
async def get_stats():
    """Returns aggregate funnel stats from StorageEngine."""
    raw = storage_engine.get_pipeline_stats()
    outreach = raw.get("outreach_status", {})
    needs_review = outreach.get("draft", 0) + outreach.get("review", 0)
    return FunnelStats(
        total_startups=raw.get("startups", 0),
        total_founders=raw.get("founders", 0),
        total_evaluated=raw.get("fit_evaluations", 0),
        fit_high=raw.get("fit_high", 0),
        fit_medium=raw.get("fit_medium", 0),
        fit_low=raw.get("fit_low", 0),
        total_drafts=raw.get("message_drafts", 0),
        needs_review=needs_review,
        outreach_draft=outreach.get("draft", 0),
        outreach_approved=outreach.get("approved", 0),
        outreach_sent=outreach.get("sent", 0),
        outreach_replied=outreach.get("replied", 0),
        outreach_rejected=outreach.get("rejected", 0),
        blacklisted=raw.get("blacklisted", 0),
    )


# ─── Batches ────────────────────────────────────────────────────

@app.get("/api/batches", response_model=List[BatchInfo])
async def get_batches():
    """Returns distinct batch names with startup counts."""
    rows = storage_engine.get_batch_list()
    return [BatchInfo(batch=r["batch"], count=r["count"]) for r in rows]


# ─── Leads (List) ──────────────────────────────────────────────

@app.get("/api/leads", response_model=LeadListResponse)
async def list_leads(
    status: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    batch: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search by company or founder name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    """Paginated, filterable lead listing."""
    leads, total = storage_engine.get_leads_paginated(
        status=status,
        fit_tier=tier,
        batch=batch,
        search_query=q,
        page=page,
        per_page=per_page,
    )
    summaries = [LeadSummary(**lead) for lead in leads]
    return LeadListResponse(leads=summaries, total=total, page=page, per_page=per_page)


# ─── Leads (Detail) ────────────────────────────────────────────

@app.get("/api/leads/{outreach_id}", response_model=LeadDetail)
async def get_lead_detail(outreach_id: int):
    """Full enriched lead dossier for human review."""
    detail = storage_engine.get_lead_detail(outreach_id)
    if not detail:
        raise HTTPException(404, f"Lead with outreach_id={outreach_id} not found")
    return LeadDetail(**detail)


# ─── Leads (Update) ────────────────────────────────────────────

@app.patch("/api/leads/{outreach_id}", response_model=UpdateLeadResponse)
async def update_lead(outreach_id: int, payload: UpdateLeadRequest):
    """Update outreach status, edit messages, or add notes."""
    detail = storage_engine.get_lead_detail(outreach_id)
    if not detail:
        raise HTTPException(404, "Lead not found")

    # 1. Update message drafts if any message fields provided
    msg_fields = {}
    if payload.linkedin_note is not None:
        msg_fields["linkedin_note"] = payload.linkedin_note
    if payload.yc_job_note is not None:
        msg_fields["yc_job_note"] = payload.yc_job_note
    if payload.cold_email_subject is not None:
        msg_fields["cold_email_subject"] = payload.cold_email_subject
    if payload.cold_email_body is not None:
        msg_fields["cold_email_body"] = payload.cold_email_body

    if msg_fields:
        storage_engine.update_message_draft(
            startup_id=detail["startup_id"],
            founder_id=detail["founder_id"],
            **msg_fields,
        )

    # 2. Update outreach record status / channel / notes
    new_status = payload.status or detail["outreach_status"]
    storage_engine.update_outreach_status(
        record_id=outreach_id,
        status=new_status,
        selected_message=payload.selected_message,
        notes=payload.notes,
    )

    # 3. Handle blacklist cascade
    if payload.status == "blacklisted":
        storage_engine.add_to_blacklist(
            identifier_type="company_name",
            identifier_value=detail["startup_name"],
            reason=payload.notes or "Blacklisted via dashboard",
        )

    return UpdateLeadResponse(success=True, message="Lead updated successfully")


# ─── Leads (Regenerate) ────────────────────────────────────────

@app.post("/api/leads/{outreach_id}/regenerate", response_model=LeadDetail)
async def regenerate_lead(
    outreach_id: int,
    payload: Optional[RegenerateRequest] = None,
):
    """Regenerate outreach message draft(s) for a lead (per-channel or all)."""
    detail = storage_engine.get_lead_detail(outreach_id)
    if not detail:
        raise HTTPException(404, f"Lead with outreach_id={outreach_id} not found")

    channel = payload.channel if payload else "all"
    pitch_angle = payload.pitch_angle_override if payload else None

    from agents.message_agent import MessageAgent
    agent = MessageAgent()

    result, err = await agent.regenerate_channel(
        startup_id=detail["startup_id"],
        founder_id=detail["founder_id"],
        channel=channel,
        pitch_angle_override=pitch_angle,
    )
    if err:
        raise HTTPException(400, f"Regeneration failed: {err}")

    updated = storage_engine.get_lead_detail(outreach_id)
    return LeadDetail(**updated)


# ─── Blacklist ──────────────────────────────────────────────────

@app.get("/api/blacklist", response_model=List[BlacklistEntryResponse])
async def list_blacklist():
    """List all blacklisted entities."""
    entries = storage_engine.get_blacklist_entries()
    return [
        BlacklistEntryResponse(
            id=e["id"],
            identifier_type=e["identifier_type"],
            identifier_value=e["identifier_value"],
            reason=e["reason"],
            created_at=str(e.get("created_at", "")),
        )
        for e in entries
    ]


@app.post("/api/blacklist", response_model=BlacklistEntryResponse)
async def add_blacklist(payload: BlacklistAddRequest):
    """Add an entity to the blacklist."""
    entry_id = storage_engine.add_to_blacklist(
        identifier_type=payload.identifier_type,
        identifier_value=payload.identifier_value,
        reason=payload.reason,
    )
    return BlacklistEntryResponse(
        id=entry_id,
        identifier_type=payload.identifier_type,
        identifier_value=payload.identifier_value,
        reason=payload.reason,
    )


@app.delete("/api/blacklist/{entry_id}", response_model=UpdateLeadResponse)
async def delete_blacklist(entry_id: int):
    """Remove an entity from the blacklist by ID."""
    # Get the entry first to find its value
    entries = storage_engine.get_blacklist_entries()
    target = None
    for e in entries:
        if e["id"] == entry_id:
            target = e
            break
    if not target:
        raise HTTPException(404, f"Blacklist entry {entry_id} not found")

    success = storage_engine.remove_from_blacklist(target["identifier_value"])
    if success:
        return UpdateLeadResponse(success=True, message="Removed from blacklist")
    raise HTTPException(500, "Failed to remove from blacklist")


# ─── Pipeline ──────────────────────────────────────────────────

@app.post("/api/pipeline/run", response_model=PipelineStatusResponse)
async def trigger_pipeline(payload: PipelineRunRequest, background_tasks: BackgroundTasks):
    """Launch exactly one operator-triggered pipeline run."""
    async with _pipeline_lock:
        if _pipeline_state["is_running"]:
            raise HTTPException(409, "A pipeline run is already in progress")

        config = PipelineConfig(
            batches=payload.batches,
            industries=payload.industries,
            limit=payload.limit,
            min_fit_score=payload.min_fit_score,
            max_concurrency=payload.max_concurrency,
            dry_run=payload.dry_run,
        )

        _pipeline_state["is_running"] = True
        _pipeline_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _pipeline_state["progress"] = "Starting..."
        _pipeline_state["session_id"] = None
        _pipeline_state["last_report"] = None

        background_tasks.add_task(_run_pipeline_background, config)

        return PipelineStatusResponse(
            is_running=True,
            started_at=_pipeline_state["started_at"],
            progress="Starting...",
        )


@app.get("/api/pipeline/status", response_model=PipelineStatusResponse)
async def get_pipeline_status():
    """Poll current pipeline execution status."""
    return PipelineStatusResponse(
        is_running=_pipeline_state["is_running"],
        session_id=_pipeline_state.get("session_id"),
        started_at=_pipeline_state.get("started_at"),
        progress=_pipeline_state.get("progress"),
        last_report=_pipeline_state.get("last_report"),
    )


@app.post("/api/pipeline/cancel")
async def cancel_pipeline():
    """Cancels the currently running pipeline execution."""
    global _current_orchestrator
    if not _pipeline_state["is_running"] or not _current_orchestrator:
        return {"success": False, "message": "No pipeline is currently running"}
    _current_orchestrator.cancel()
    _pipeline_state["progress"] = "Cancelling pipeline..."
    return {"success": True, "message": "Cancellation signal sent"}


@app.get("/api/pipeline/last-config", response_model=PipelineRunConfigResponse)
async def get_last_pipeline_config():
    """Returns the parameters of the most recent pipeline run for 1-click 'Run Again'."""
    last = storage_engine.get_last_pipeline_run_config()
    if not last:
        return PipelineRunConfigResponse()
    return PipelineRunConfigResponse(
        batch=last.get("batch") or "Fall 2026",
        industry=last.get("industry"),
        startup_limit=last.get("startup_limit") or 5,
        min_fit_score=last.get("min_fit_score") if last.get("min_fit_score") is not None else 50,
        max_concurrency=last.get("max_concurrency") or 5,
        dry_run=bool(last.get("dry_run")),
        status=last.get("status"),
        started_at=last.get("started_at"),
    )


@app.get("/api/pipeline/history", response_model=List[PipelineRunHistoryItem])
async def get_pipeline_history(limit: int = Query(20, ge=1, le=100)):
    """Returns recent pipeline execution history sorted by started_at DESC."""
    raw_history = storage_engine.get_pipeline_run_history(limit=limit)
    return [
        PipelineRunHistoryItem(
            id=r["id"],
            session_id=r["session_id"],
            batch=r["batch"],
            industry=r.get("industry"),
            startup_limit=r.get("startup_limit", 5),
            min_fit_score=r.get("min_fit_score", 50),
            max_concurrency=r.get("max_concurrency", 5),
            dry_run=bool(r.get("dry_run", False)),
            status=r["status"],
            progress_message=r.get("progress_message"),
            started_at=r.get("started_at"),
            completed_at=r.get("completed_at"),
            duration_seconds=r.get("duration_seconds"),
            stats_json=r.get("stats_json"),
            error_summary=r.get("error_summary"),
        )
        for r in raw_history
    ]


async def _run_pipeline_background(config: PipelineConfig):
    """Runs the MasterOrchestrator and updates _pipeline_state."""
    global _current_orchestrator

    def on_progress(event: str, payload: dict):
        if event == "stage_start":
            stage = payload.get("stage", "").upper()
            _pipeline_state["progress"] = f"Running: {stage}"
        elif event == "stage_complete":
            stage = payload.get("stage", "").upper()
            _pipeline_state["progress"] = f"Completed: {stage}"

    try:
        orchestrator = MasterOrchestrator(config=config, progress_callback=on_progress)
        _current_orchestrator = orchestrator
        _pipeline_state["session_id"] = orchestrator.session_id
        report = await orchestrator.run()
        _pipeline_state["last_report"] = report.model_dump(mode="json")
        if orchestrator.cancel_event.is_set():
            _pipeline_state["progress"] = "Pipeline cancelled"
        else:
            _pipeline_state["progress"] = "Pipeline complete" if report.success else "Pipeline failed"
    except Exception as e:
        logger.error(f"Pipeline background task failed: {e}")
        _pipeline_state["progress"] = f"Pipeline failed: {e}"
        _pipeline_state["last_report"] = None
    finally:
        _current_orchestrator = None
        _pipeline_state["is_running"] = False


# ─── Export ─────────────────────────────────────────────────────

EXPORT_COLUMNS = [
    "startup_name", "slug", "batch", "founder_name", "founder_title",
    "linkedin_url", "fit_score", "fit_tier", "contribution_angle",
    "linkedin_note", "yc_job_note", "cold_email_subject", "cold_email_body",
    "outreach_status",
]


@app.get("/api/export")
async def export_leads(
    min_score: int = Query(50, ge=0, le=100),
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    """Export qualified leads as CSV or JSON file download."""
    leads = storage_engine.get_qualified_leads_for_export(min_score=min_score)

    if format == "json":
        content = json.dumps(leads, indent=2, ensure_ascii=False, default=str)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=leads_score_{min_score}.json"},
        )

    # CSV export
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=leads_score_{min_score}.csv"},
    )


# ─── Phase 6: Health & Diagnostics ─────────────────────────────

@app.get("/health")
async def render_health():
    """Minimal unauthenticated health endpoint for Render infrastructure probes."""
    return {"status": "ok"}

@app.get("/api/health", response_model=HealthResponse)
async def check_health():
    """Runs end-to-end diagnostics on PostgreSQL, Redis, OpenRouter, and Pipeline."""
    res = await health_checker.check_all()
    components = {}
    for name, comp in res.get("components", {}).items():
        components[name] = ComponentHealth(
            status=comp.get("status", "unknown"),
            latency_ms=comp.get("latency_ms"),
            details=comp.get("details", {}),
            error=comp.get("error"),
        )
    return HealthResponse(
        status=res.get("status", "healthy"),
        components=components,
        version=res.get("version", "1.0.0"),
        environment=res.get("environment", "production"),
    )


# ─── Phase 6: Structured Logging ───────────────────────────────

@app.get("/api/logs", response_model=LogsResponse)
async def get_logs(
    limit: int = Query(100, ge=1, le=1000),
    level: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
):
    """Retrieves buffered structured log lines from the in-memory ring buffer."""
    entries = global_log_buffer.get_logs(limit=limit, level=level, since=since)
    return LogsResponse(
        logs=[
            LogEntry(
                timestamp=e["timestamp"],
                level=e["level"],
                logger=e["logger"],
                message=e["message"],
            )
            for e in entries
        ],
        total=len(entries),
    )


# ─── Phase 6: Backups ──────────────────────────────────────────

@app.post("/api/backup", response_model=BackupCreateResponse)
async def create_backup():
    """Generates a compressed JSON backup snapshot of the database."""
    res = backup_engine.create_backup()
    return BackupCreateResponse(
        success=res.get("success", False),
        message="Backup created successfully" if res.get("success") else "Backup creation failed",
        filename=res.get("filename"),
        size_bytes=res.get("size_bytes"),
        table_counts=res.get("table_counts"),
        error=res.get("error"),
    )


@app.get("/api/backups", response_model=List[BackupItem])
async def list_backups():
    """Lists available backup snapshots in the data/backups directory."""
    backups = backup_engine.list_backups()
    return [
        BackupItem(
            filename=b["filename"],
            size_bytes=b["size_bytes"],
            created_at=b["created_at"],
            table_counts=b.get("table_counts", {}),
        )
        for b in backups
    ]


@app.post("/api/backups/restore")
async def restore_backup(payload: BackupRestoreRequest):
    """Restores database state from a specified backup snapshot."""
    filename = os.path.basename(payload.filename)
    if filename != payload.filename:
        raise HTTPException(400, "Invalid backup filename")
    res = backup_engine.restore_backup(filename)
    if not res.get("success"):
        raise HTTPException(400, res.get("error", "Restore failed"))
    return res

