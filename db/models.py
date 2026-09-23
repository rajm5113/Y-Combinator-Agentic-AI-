"""Pydantic data models and typed runtime contracts for all system entities.

Ensures fail-fast schema validation across memory layers and agents.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StartupCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: str = Field(..., min_length=1)
    batch: str = Field(..., min_length=1)
    website: Optional[str] = None
    one_liner: Optional[str] = None
    long_description: Optional[str] = None
    team_size: Optional[int] = None
    industry: Optional[str] = None
    subindustry: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    status: str = Field(default="active")
    is_hiring: bool = Field(default=False)
    yc_url: Optional[str] = None
    jobs_data: List[Dict[str, Any]] = Field(default_factory=list)
    primary_location_country: Optional[str] = None
    primary_location_state: Optional[str] = None
    primary_location_city: Optional[str] = None
    office_locations: List[Dict[str, Any]] = Field(default_factory=list)
    location_source: Optional[str] = None
    location_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Startup(StartupCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    discovered_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FounderCreate(BaseModel):
    startup_id: int
    full_name: str = Field(..., min_length=1)
    title: Optional[str] = None
    bio: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    has_email: bool = Field(default=False)
    email: Optional[str] = None
    avatar_url: Optional[str] = None


class Founder(FounderCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    extracted_at: datetime = Field(default_factory=utc_now)


class FitEvaluation(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    startup_id: int
    score: int = Field(..., ge=0, le=100)
    fit_tier: Literal["HIGH", "MEDIUM", "LOW"]
    should_contact: bool = Field(default=False)
    match_rationale: List[str] = Field(default_factory=list)
    contribution_angle: Optional[str] = None
    model_used: Optional[str] = None
    evaluated_at: datetime = Field(default_factory=utc_now)

    @field_validator("fit_tier", mode="before")
    def compute_fit_tier(cls, v, info):
        if v:
            return v.upper()
        return v


class MessageDrafts(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    startup_id: int
    founder_id: int
    linkedin_note: str = Field(..., max_length=300)
    yc_job_note: Optional[str] = None
    cold_email_subject: Optional[str] = None
    cold_email_body: Optional[str] = None
    model_used: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class OutreachRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    startup_id: int
    founder_id: int
    status: Literal["discovered", "draft", "approved", "sent", "replied", "rejected", "blacklisted"] = "discovered"
    active_channel: Literal["linkedin", "yc_job", "email"] = "linkedin"
    selected_message: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BlacklistEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    identifier_type: Literal["founder_name", "linkedin_url", "company_name", "domain"]
    identifier_value: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class ImplicitInsight(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    entity_type: Literal["startup", "founder", "industry"]
    entity_key: str = Field(..., min_length=1)
    insight_key: str = Field(..., min_length=1)
    insight_value: Dict[str, Any]
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    updated_at: datetime = Field(default_factory=utc_now)
