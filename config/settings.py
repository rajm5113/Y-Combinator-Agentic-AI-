"""Centralized system configuration loaded from environment variables (.env).

Enforces strict Pydantic validation, model catalog tiering, and paths.
"""

from pathlib import Path
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base Paths
    project_root: Path = BASE_DIR
    config_dir: Path = BASE_DIR / "config"
    data_dir: Path = BASE_DIR / "data"
    profile_path: Path = BASE_DIR / "config" / "profile.json"
    sqlite_db_path: Path = BASE_DIR / "data" / "outreach.db"

    # OpenRouter API Configuration
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    app_name: str = Field(default="YC Founder Outreach Agent", validation_alias="APP_NAME")
    site_url: str = Field(default="https://localhost", validation_alias="SITE_URL")

    # Database & Cache URLs
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/yc_outreach",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias="REDIS_URL",
    )
    use_sqlite_fallback: bool = Field(
        default=True,
        description="Enable automatic local SQLite fallback if PostgreSQL is unavailable",
    )
    use_inmemory_cache_fallback: bool = Field(
        default=True,
        description="Enable local dictionary cache fallback if Redis is unavailable",
    )

    # Model Priority Catalogs by Capability Tier
    reasoning_fallback_chain: List[str] = Field(
        default=[
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "thinkingmachines/inkling:free",
            "nex-agi/nex-n2.5-pro:free",
            "nex-agi/nex-n2.5-mini:free",
        ],
        description="Priority chain for deep planning and reasoning",
    )

    extraction_fallback_chain: List[str] = Field(
        default=[
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
            "nex-agi/nex-n2.5-mini:free",
            "nex-agi/nex-n2.5-pro:free",
        ],
        description="Priority chain for structured data extraction and tool calling",
    )

    fast_fallback_chain: List[str] = Field(
        default=[
            "inclusionai/ling-3.0-flash:free",
            "google/gemma-4-26b-a4b-it:free",
            "nex-agi/nex-n2.5-mini:free",
            "nex-agi/nex-n2.5-pro:free",
        ],
        description="Priority chain for fast routing and classification",
    )

    # Rate Limiting & Backoff Configuration
    max_retries_per_model: int = Field(default=2, ge=1)
    retry_base_delay_seconds: float = Field(default=1.0, ge=0.1)
    retry_max_delay_seconds: float = Field(default=8.0, ge=1.0)
    jitter_factor: float = Field(default=0.25, ge=0.0, le=1.0)

    # Targeting & Filtering Defaults
    default_batches: List[str] = Field(
        default=["Fall 2026", "Summer 2026", "Winter 2026"]
    )
    default_industries: List[str] = Field(
        default=["B2B", "Developer Tools", "AI/ML", "Analytics", "SaaS"]
    )
    fit_threshold_high: int = Field(default=75, ge=0, le=100)
    fit_threshold_medium: int = Field(default=50, ge=0, le=100)

    # Server Defaults
    server_host: str = Field(default="127.0.0.1")
    server_port: int = Field(default=8000)

    # Phase 2: HTTP Client & Rate Governance
    http_max_connections: int = Field(default=10, ge=1)
    http_inter_request_delay: float = Field(
        default=0.5, ge=0.0,
        description="Politeness delay in seconds between consecutive HTTP requests",
    )
    http_timeout_seconds: float = Field(default=15.0, ge=1.0)
    http_max_retries: int = Field(default=3, ge=1)

    # Phase 2: YC Algolia Public Search API
    algolia_app_id: str = Field(
        default="45bwzj1sgc",
        description="YC Algolia Application ID (public, embedded in frontend JS)",
    )
    algolia_api_key: str = Field(
        default="Zjk9gs3pOF3ek4OjFbMGitSfRJlfGixw",
        description="YC Algolia public read-only search key",
    )
    algolia_index: str = Field(default="YCCompany_production")
    yc_base_url: str = Field(
        default="https://www.ycombinator.com",
        description="YC website base URL for company page fetching",
    )

    # Phase 4: Pipeline Orchestration
    pipeline_max_concurrency: int = Field(
        default=5, ge=1, le=20,
        description="Default bounded parallelism for pipeline stages",
    )
    pipeline_default_min_score: int = Field(
        default=50, ge=0, le=100,
        description="Default qualification gate threshold",
    )
    pipeline_session_ttl: int = Field(
        default=86400,
        description="Redis session state TTL in seconds (24h)",
    )

    # Messaging Configuration
    linkedin_note_max_chars: int = Field(
        default=300,
        ge=100,
        le=300,
        description="Maximum LinkedIn connection-note length",
    )

    # Phase 5: Dashboard
    dashboard_host: str = Field(
        default="127.0.0.1",
        description="Dashboard server bind address (localhost only for security)",
    )
    dashboard_port: int = Field(
        default=8501, ge=1024, le=65535,
        description="Dashboard server port",
    )
    dashboard_auto_open_browser: bool = Field(
        default=True,
        description="Automatically open browser when dashboard launches",
    )


settings = Settings()
