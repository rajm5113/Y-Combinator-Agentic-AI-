"""Pydantic request/response schemas for all Dashboard API endpoints."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# --- Stats & Funnel ---

class FunnelStats(BaseModel):
    """Aggregate metrics for the dashboard overview cards."""
    total_startups: int = 0
    total_founders: int = 0
    total_evaluated: int = 0
    fit_pending: int = 0
    fit_high: int = 0
    fit_medium: int = 0
    fit_low: int = 0
    total_drafts: int = 0
    needs_review: int = 0
    outreach_draft: int = 0
    outreach_approved: int = 0
    outreach_sent: int = 0
    outreach_replied: int = 0
    outreach_rejected: int = 0
    blacklisted: int = 0


class BatchInfo(BaseModel):
    """YC batch catalog item plus how many records are already stored locally."""
    batch: str
    count: int = 0
    code: Optional[str] = None


# --- Lead List & Detail ---

class LeadSummary(BaseModel):
    """Compact lead row for the leads table view."""
    outreach_id: int
    startup_name: str
    slug: str
    batch: str
    primary_location_country: Optional[str] = None
    primary_location_city: Optional[str] = None
    founder_name: str
    founder_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    fit_score: int = 0
    fit_tier: str = "UNKNOWN"
    outreach_status: str = "discovered"
    active_channel: str = "linkedin"
    has_drafts: bool = False


class LeadDetail(BaseModel):
    """Full lead dossier for the detail/review view."""
    outreach_id: int
    startup_id: int
    founder_id: int

    # Company
    startup_name: str
    slug: str
    batch: str
    primary_location_country: Optional[str] = None
    primary_location_city: Optional[str] = None
    office_locations: List[Dict[str, Any]] = Field(default_factory=list)
    website: Optional[str] = None
    one_liner: Optional[str] = None
    long_description: Optional[str] = None
    industry: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_hiring: bool = False
    jobs_data: List[Dict[str, Any]] = Field(default_factory=list)

    # Founder
    founder_name: str
    founder_title: Optional[str] = None
    founder_bio: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None

    # Fit
    fit_score: int = 0
    fit_tier: str = "UNKNOWN"
    match_rationale: List[str] = Field(default_factory=list)
    contribution_angle: Optional[str] = None

    # Messages
    linkedin_note: Optional[str] = None
    yc_job_note: Optional[str] = None
    cold_email_subject: Optional[str] = None
    cold_email_body: Optional[str] = None

    # Outreach state
    outreach_status: str = "discovered"
    active_channel: str = "linkedin"
    selected_message: Optional[str] = None
    notes: Optional[str] = None


class LeadListResponse(BaseModel):
    """Paginated lead list response."""
    leads: List[LeadSummary]
    total: int
    page: int = 1
    per_page: int = 25


# --- Lead Actions ---

class UpdateLeadRequest(BaseModel):
    """Payload for updating a lead's outreach state or message edits."""
    status: Optional[Literal[
        "discovered", "draft", "review", "approved", "sent", "replied", "rejected", "blacklisted"
    ]] = None
    active_channel: Optional[Literal["linkedin", "yc_job", "email"]] = None
    selected_message: Optional[str] = None
    notes: Optional[str] = None
    # Editable message fields — human can revise before sending
    linkedin_note: Optional[str] = None
    yc_job_note: Optional[str] = None
    cold_email_subject: Optional[str] = None
    cold_email_body: Optional[str] = None


class RegenerateRequest(BaseModel):
    """Payload for regenerating message draft(s) per channel or all."""
    channel: Literal["linkedin_note", "yc_job_note", "cold_email", "all"] = "all"
    pitch_angle_override: Optional[str] = None


class UpdateLeadResponse(BaseModel):
    success: bool
    message: str


# --- Blacklist ---

class BlacklistAddRequest(BaseModel):
    identifier_type: Literal["company_name", "founder_name", "linkedin_url", "domain"]
    identifier_value: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class BlacklistEntryResponse(BaseModel):
    id: int
    identifier_type: str
    identifier_value: str
    reason: str
    created_at: Optional[str] = None


# --- Pipeline Trigger ---

class PipelineRunRequest(BaseModel):
    """Trigger a pipeline run from the dashboard."""
    batches: List[str] = Field(default_factory=lambda: ["Fall 2026"])
    industries: List[str] = Field(default_factory=list)
    target_country: str = "India"
    target_city: str = ""
    target_location_mode: Literal["office_or_job", "office_only", "job_only"] = "office_or_job"
    limit: Optional[int] = Field(default=5, ge=1, le=500)
    min_fit_score: int = Field(default=50, ge=0, le=100)
    max_concurrency: int = Field(default=5, ge=1, le=20)
    dry_run: bool = False


class PipelineStatusResponse(BaseModel):
    """Current pipeline execution status."""
    is_running: bool
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    progress: Optional[str] = None
    last_report: Optional[Dict[str, Any]] = None


# --- Phase 6: Health & Diagnostics ---

class ComponentHealth(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    latency_ms: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    components: Dict[str, ComponentHealth] = Field(default_factory=dict)
    version: str = "1.0.0"
    environment: str = "production"


# --- Phase 6: Run History & Quick Re-run ---

class PipelineRunConfigResponse(BaseModel):
    batch: str = "Fall 2026"
    industry: Optional[str] = None
    target_country: str = "India"
    target_city: str = ""
    target_location_mode: str = "office_or_job"
    startup_limit: int = 5
    min_fit_score: int = 50
    max_concurrency: int = 5
    dry_run: bool = False
    status: Optional[str] = None
    started_at: Optional[str] = None


class PipelineRunHistoryItem(BaseModel):
    id: int
    session_id: str
    batch: str
    industry: Optional[str] = None
    target_country: str = "India"
    target_city: str = ""
    target_location_mode: str = "office_or_job"
    startup_limit: int = 5
    min_fit_score: int = 50
    max_concurrency: int = 5
    dry_run: bool = False
    status: str
    progress_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    stats_json: Optional[Dict[str, Any]] = None
    error_summary: Optional[str] = None


# --- Phase 6: Structured Logging ---

class LogEntry(BaseModel):
    timestamp: str
    level: str
    logger: str
    message: str


class LogsResponse(BaseModel):
    logs: List[LogEntry]
    total: int


# --- Phase 6: Backups ---

class BackupItem(BaseModel):
    filename: str
    size_bytes: int
    created_at: str
    table_counts: Dict[str, int] = Field(default_factory=dict)


class BackupCreateResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None
    size_bytes: Optional[int] = None
    table_counts: Optional[Dict[str, int]] = None
    error: Optional[str] = None


class BackupRestoreRequest(BaseModel):
    filename: str

