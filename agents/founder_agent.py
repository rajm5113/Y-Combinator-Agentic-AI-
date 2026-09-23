"""Founder Intel & Social Extraction Agent — extracts verified founder profiles,
bios, LinkedIn URLs, and active job postings from YC structured Inertia.js data.

Zero DOM scraping. Zero fragile CSS selectors.
Degrades gracefully to Tier 2 LLM extraction only when structured Inertia.js data is missing.
"""

import asyncio
import html as html_module
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from agents.base import AgentResult, BaseAgent
from agents.http_client import ResilientHTTPClient
from config.llm_client import ModelTier, ResilientLLMClient, llm_client as default_llm_client
from config.settings import settings
from location_utils import extract_company_locations, extract_job_locations, primary_location
from db.memory import MemoryManager, memory_manager as default_memory_manager
from db.models import FounderCreate, StartupCreate
from db.storage import StorageEngine, storage_engine as default_storage_engine

logger = logging.getLogger("founder_agent")


# ─── URL Normalization & Domain Extraction ──────────────────────────────────

def normalize_linkedin_url(raw_url: Optional[str]) -> Optional[str]:
    """Standardizes LinkedIn URLs to canonical format (https://www.linkedin.com/in/{handle}).

    Handles query tracking parameters, missing schemes, www prefixes, and non-LinkedIn URLs.
    Returns None if the URL is empty or does not belong to linkedin.com.
    """
    if not raw_url:
        return None

    url = raw_url.strip()
    if not url:
        return None

    # Ensure https scheme
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Validate that hostname belongs to LinkedIn
    netloc = parsed.netloc.lower()
    if netloc != "linkedin.com" and not netloc.endswith(".linkedin.com"):
        return None

    clean_path = parsed.path.rstrip("/")
    if not clean_path or clean_path == "/":
        return None

    # Reconstruct clean canonical URL without query params or fragments
    return urlunparse((
        "https",
        "www.linkedin.com",
        clean_path,
        "",
        "",
        "",
    ))


def extract_domain(url: Optional[str]) -> Optional[str]:
    """Extracts a normalized domain name from a URL for blacklist checking."""
    if not url:
        return None
    try:
        clean = url.strip()
        if not clean.startswith(("http://", "https://")):
            clean = f"https://{clean}"
        parsed = urlparse(clean)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or None
    except Exception:
        return None


# ─── Inertia.js JSON Extraction ─────────────────────────────────────────────

def extract_inertia_data(html_text: str) -> Optional[Dict[str, Any]]:
    """Extracts the structured Inertia.js data-page JSON payload from raw HTML.

    Handles raw quotes, double quotes, and HTML-entity-encoded JSON (&quot;).
    """
    if not html_text:
        return None

    # 1. Standard Inertia.js data-page attribute patterns
    patterns = [
        r'<div[^>]*id=["\']app["\'][^>]*data-page=([\'"])(.*?)\1',
        r'<div[^>]*data-page=([\'"])(.*?)\1[^>]*id=["\']app["\']',
        r'data-page=([\'"])([\s\S]*?)\1',
    ]

    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if match:
            raw_content = match.group(2)
            # Try parsing raw content
            try:
                return json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                pass

            # Try parsing unescaped HTML entities
            try:
                unescaped = html_module.unescape(raw_content)
                return json.loads(unescaped)
            except (json.JSONDecodeError, TypeError):
                pass

    # 2. Alternative Next.js / embedded script tags as fallback
    next_pattern = r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>([\s\S]*?)</script>'
    next_match = re.search(next_pattern, html_text)
    if next_match:
        try:
            return json.loads(next_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# ─── LLM Fallback Extraction ────────────────────────────────────────────────

async def llm_fallback_extraction(
    html_text: str,
    slug: str,
    llm: Optional[ResilientLLMClient] = None,
) -> Optional[Dict[str, Any]]:
    """Degraded path: uses Tier 2 Extraction models to parse company & founder details

    Activated only when Inertia.js data-page is missing or invalid. Emits a WARNING.
    """
    client = llm or default_llm_client
    logger.warning(f"⚠️ Used LLM fallback extraction for startup '{slug}' - Inertia.js structure changed or missing")

    truncated_html = html_text[:8000]
    messages = [
        {
            "role": "system",
            "content": (
                "You are a structured data extraction engine. Extract company, founder, "
                "and job information from the provided YC company page HTML. "
                "Return a valid JSON object with the key 'company' containing: "
                "name (str), website (str), one_liner (str), long_description (str), "
                "batch_name (str), founders (list of objects with full_name, title, bio, linkedin_url, twitter_url), "
                "jobs (list of objects with title, location, remote)."
            ),
        },
        {
            "role": "user",
            "content": f"Extract founders, company description, and job listings from this YC page for '{slug}':\n\n{truncated_html}",
        },
    ]

    try:
        response = await asyncio.to_thread(
            client.call_with_fallback,
            tier=ModelTier.EXTRACTION,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
        )
        content = response.get("content")
        if not content:
            return None
        parsed = json.loads(content)
        return parsed
    except Exception as e:
        logger.error(f"LLM fallback extraction failed for '{slug}': {e}")
        return None


# ─── Founder Intel Agent ────────────────────────────────────────────────────

class FounderAgent(BaseAgent):
    """Extracts verified founder profiles and job postings from YC company pages.

    Execution Flow:
    1. Check Cache Memory for previously extracted company JSON (TTL: 12h).
    2. Fetch YC company page HTML via ResilientHTTPClient.
    3. Extract Inertia.js data-page JSON payload (100% structured data).
    4. If Inertia payload missing, invoke Tier 2 LLM fallback extraction.
    5. Parse founders, normalize LinkedIn URLs, and run Blacklist Gate.
    6. Upsert clean Founder records into StorageEngine.
    7. Parse active jobs, update startup is_hiring flag and jobs_data.
    8. Cache results and return AgentResult.
    """

    agent_name = "founder"
    description = "Extracts verified founder profiles, LinkedIn URLs, bios, and active jobs from YC pages"
    required_tools = ["extract_company_profile", "verify_founder_socials"]
    CACHE_TTL = 43200  # 12 hours

    def __init__(
        self,
        http_client: Optional[ResilientHTTPClient] = None,
        llm_client: Optional[ResilientLLMClient] = None,
        storage: Optional[StorageEngine] = None,
        memory: Optional[MemoryManager] = None,
    ):
        self.http_client = http_client
        self.llm_client = llm_client or default_llm_client
        self.storage = storage or default_storage_engine
        self.memory = memory or default_memory_manager

    async def _get_http_client(self) -> ResilientHTTPClient:
        """Lazily creates or returns the HTTP client."""
        if self.http_client is None:
            self.http_client = ResilientHTTPClient()
        return self.http_client

    async def execute(self, context: Dict[str, Any]) -> AgentResult:
        """Main founder extraction execution.

        Context parameters:
            startup_slugs: Optional[List[str]] — Slugs of startups to extract founders for.
                           If not provided, queries storage for startups without founders.
            force_refresh: bool — If True, bypasses Cache Memory.
        """
        start_time = time.time()
        http = await self._get_http_client()

        # Determine target startup slugs
        slugs = context.get("startup_slugs") or context.get("slugs")
        if not slugs:
            # Automatic context discovery from business memory
            unprocessed = self.storage.get_startups_without_founders()
            slugs = [s["slug"] for s in unprocessed if s.get("slug")]

        force_refresh = context.get("force_refresh", False)

        errors: List[str] = []
        stats: Dict[str, int] = {
            "startups_processed": 0,
            "founders_extracted": 0,
            "founders_new": 0,
            "founders_updated": 0,
            "blacklisted_skips": 0,
            "linkedin_urls_found": 0,
            "llm_fallback_invocations": 0,
            "extraction_failures": 0,
            "cached_hits": 0,
        }
        extracted_data: List[Dict[str, Any]] = []

        for slug in slugs:
            cached_company = None
            if not force_refresh:
                cached_company = self.memory.get_cached_tool_result(
                    "extract_company_profile", {"slug": slug}
                )

            company_info: Optional[Dict[str, Any]] = None

            if cached_company:
                company_info = cached_company
                stats["cached_hits"] += 1
            else:
                url = f"{settings.yc_base_url}/companies/{slug}"
                res = await http.get(url)

                if not res.success or not res.data:
                    msg = f"Startup {slug}: HTTP fetch failed ({res.status_code}): {res.error}"
                    errors.append(msg)
                    stats["extraction_failures"] += 1
                    continue

                html_content = res.data if isinstance(res.data, str) else str(res.data)
                page_data = extract_inertia_data(html_content)

                if page_data:
                    # Resolve company object from Inertia props
                    props = page_data.get("props", {})
                    company_info = props.get("company") or page_data.get("company") or page_data
                else:
                    # Degradation to LLM fallback
                    stats["llm_fallback_invocations"] += 1
                    llm_data = await llm_fallback_extraction(html_content, slug, self.llm_client)
                    if llm_data:
                        company_info = llm_data.get("company") or llm_data
                        errors.append(f"Startup {slug}: Inertia.js extraction failed, used LLM fallback")
                    else:
                        errors.append(f"Startup {slug}: Extraction failed (Inertia.js and LLM fallback failed)")
                        stats["extraction_failures"] += 1
                        continue

                # Cache extracted structured company profile
                if company_info:
                    self.memory.cache_tool_result(
                        "extract_company_profile",
                        {"slug": slug},
                        company_info,
                        ttl=self.CACHE_TTL,
                    )

            if not company_info:
                continue

            # Resolve current office/company locations and job locations.
            office_locations = extract_company_locations(company_info)
            raw_jobs = company_info.get("jobs", []) or []
            job_locations = extract_job_locations(raw_jobs)
            primary = primary_location(office_locations or job_locations)
            location_source = "yc_company_page" if office_locations else (
                "yc_job_listing" if job_locations else None
            )

            # Resolve or initialize startup in database
            startup_record = self.storage.get_startup_by_slug(slug)
            if not startup_record:
                startup_create = StartupCreate(
                    name=company_info.get("name") or slug.replace("-", " ").title(),
                    slug=slug,
                    batch=company_info.get("batch_name") or company_info.get("batch") or "Unknown",
                    website=company_info.get("website"),
                    one_liner=company_info.get("one_liner"),
                    long_description=company_info.get("long_description"),
                    team_size=company_info.get("team_size"),
                    status="active",
                    yc_url=f"{settings.yc_base_url}/companies/{slug}",
                    primary_location_country=primary["country"],
                    primary_location_state=primary["state"],
                    primary_location_city=primary["city"],
                    office_locations=office_locations,
                    location_source=location_source,
                    location_confidence=0.9 if office_locations else (0.7 if job_locations else 0.0),
                )
                startup_id = self.storage.upsert_startup(startup_create)
                startup_record = self.storage.get_startup_by_slug(slug)
            else:
                startup_id = startup_record["id"]

            # Process founders
            raw_founders = company_info.get("founders", []) or []
            saved_founders: List[Dict[str, Any]] = []

            for f in raw_founders:
                full_name = f.get("full_name") or f.get("name")
                if not full_name:
                    continue

                raw_linkedin = f.get("linkedin_url")
                clean_linkedin = normalize_linkedin_url(raw_linkedin)
                if clean_linkedin:
                    stats["linkedin_urls_found"] += 1

                # Blacklist Pre-check (Input Guardrail)
                is_blocked = self.storage.is_blacklisted(
                    founder_name=full_name,
                    linkedin_url=clean_linkedin,
                    company_name=startup_record.get("name") if startup_record else None,
                    domain=extract_domain(startup_record.get("website") if startup_record else None),
                )
                if is_blocked:
                    logger.info(f"⛔ Skipping blacklisted entity: {full_name} at {slug}")
                    stats["blacklisted_skips"] += 1
                    continue

                existing_founders = self.storage.get_founders_by_startup_id(startup_id)
                was_existing = any(
                    ef["full_name"].strip().lower() == full_name.strip().lower()
                    for ef in existing_founders
                )

                founder_create = FounderCreate(
                    startup_id=startup_id,
                    full_name=full_name.strip(),
                    title=f.get("title"),
                    bio=f.get("bio"),
                    linkedin_url=clean_linkedin,
                    twitter_url=f.get("twitter_url") or f.get("twitter"),
                    avatar_url=f.get("avatar_thumb") or f.get("avatar_url"),
                    has_email=bool(f.get("email")),
                    email=f.get("email"),
                )
                founder_id = self.storage.upsert_founder(founder_create)
                # Every verified founder becomes a discovery-stage lead row.
                self.storage.ensure_outreach_record(startup_id, founder_id)
                stats["founders_extracted"] += 1

                if was_existing:
                    stats["founders_updated"] += 1
                else:
                    stats["founders_new"] += 1

                saved_founders.append({
                    "founder_id": founder_id,
                    "full_name": full_name,
                    "title": f.get("title"),
                    "bio": f.get("bio"),
                    "linkedin_url": clean_linkedin,
                    "twitter_url": f.get("twitter_url") or f.get("twitter"),
                })

            # Process jobs and update startup hiring status
            if raw_jobs or company_info.get("website"):
                # Update startup metadata with jobs
                is_hiring = True if len(raw_jobs) > 0 else (startup_record.get("is_hiring", 0) == 1)
                startup_update = StartupCreate(
                    name=company_info.get("name") or startup_record["name"],
                    slug=slug,
                    batch=company_info.get("batch_name") or startup_record["batch"],
                    website=company_info.get("website") or startup_record.get("website"),
                    one_liner=company_info.get("one_liner") or startup_record.get("one_liner"),
                    long_description=company_info.get("long_description") or startup_record.get("long_description"),
                    team_size=company_info.get("team_size") or startup_record.get("team_size"),
                    industry=startup_record.get("industry"),
                    subindustry=startup_record.get("subindustry"),
                    tags=startup_record.get("tags") or [],
                    status=startup_record.get("status") or "active",
                    is_hiring=is_hiring,
                    yc_url=f"{settings.yc_base_url}/companies/{slug}",
                    jobs_data=raw_jobs,
                    primary_location_country=primary["country"] or startup_record.get("primary_location_country"),
                    primary_location_state=primary["state"] or startup_record.get("primary_location_state"),
                    primary_location_city=primary["city"] or startup_record.get("primary_location_city"),
                    office_locations=office_locations or (
                        json.loads(startup_record.get("office_locations") or "[]")
                        if startup_record.get("office_locations") else []
                    ),
                    location_source=location_source or startup_record.get("location_source"),
                    location_confidence=0.9 if office_locations else (0.7 if job_locations else float(startup_record.get("location_confidence") or 0.0)),
                )
                self.storage.upsert_startup(startup_update)

            extracted_data.append({
                "startup_id": startup_id,
                "startup_slug": slug,
                "founders": saved_founders,
                "jobs": raw_jobs,
            })
            stats["startups_processed"] += 1

        duration = round(time.time() - start_time, 2)
        return AgentResult(
            success=stats["extraction_failures"] == 0,
            agent_name=self.agent_name,
            data=extracted_data,
            errors=errors,
            stats=stats,
            duration_seconds=duration,
        )
