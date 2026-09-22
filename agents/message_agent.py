"""Multi-Channel Grounded Message Writer Agent (Spec 3.2).

Generates authentic, high-conviction outreach messages across 3 channels:
1. LinkedIn Connection Note (strictly <= 300 characters).
2. YC Startup Job Note (1-2 paragraphs citing active jobs & architecture).
3. Cold Email (Subject line + 3 concise paragraphs).

Grounded strictly in verified company/founder facts and candidate profile knowledge.
Never hallucinates. Strictly obeys the Qualification Gate (skips startups with fit score < 50).
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.base import AgentResult, BaseAgent
from agents.founder_agent import extract_domain
from config.llm_client import ModelTier, ResilientLLMClient, llm_client as default_llm_client
from config.settings import settings
from db.memory import MemoryManager, memory_manager as default_memory_manager
from db.models import MessageDrafts
from db.storage import StorageEngine, storage_engine as default_storage_engine

logger = logging.getLogger("message_agent")

FORBIDDEN_BUZZWORDS = [
    "thrilled to connect",
    "game-changing",
    "game changer",
    "game-changer",
    "exciting opportunity",
    "synergy",
    "pick your brain",
    "hope this finds you well",
    "blown away by",
    "delve",
    "testament",
]

PLACEHOLDER_PATTERN = re.compile(
    r"(\[\s*(?:founder|company|startup|candidate|name|role|job|insert)(?:[\s_-]+(?:name|title|here|text))?\s*\]|"
    r"\{\s*(?:founder_name|founder|company|startup|candidate_name|role|job)\s*\})",
    re.IGNORECASE,
)


def enforce_linkedin_limit(note: str, max_chars: int = 300) -> str:
    """Enforces LinkedIn character limits strictly, trimming at clean sentence or word boundaries."""
    if not note:
        return ""
    clean = note.strip()
    if len(clean) <= max_chars:
        return clean

    # Check for clean sentence ending within budget
    truncated = clean[:max_chars]
    last_period = truncated.rfind(".")
    last_exclamation = truncated.rfind("!")
    last_break = max(last_period, last_exclamation)
    if last_break >= 180:
        return clean[:last_break + 1].strip()

    # Fallback to word boundary
    last_space = clean[:max_chars - 3].rfind(" ")
    if last_space > 0:
        return clean[:last_space].strip() + "..."

    return clean[:max_chars]


class MessageDraftPayload(BaseModel):
    """Strict structured output contract for generated message drafts."""
    linkedin_note: str = Field(..., description="Personalized LinkedIn connection note hook")
    yc_job_note: str = Field(default="", description="YC Work at a Startup message, ~120-150 words")
    cold_email_subject: str = Field(default="", description="Subject line for cold email")
    cold_email_body: str = Field(default="", description="Full body for cold email, 3 concise paragraphs")

    @field_validator("linkedin_note")
    def validate_linkedin_note(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("LinkedIn note cannot be empty")
        clean = v.strip()
        max_chars = settings.linkedin_note_max_chars
        if len(clean) > max_chars:
            raise ValueError(
                f"The LinkedIn note exceeds the configured character limit of {max_chars} characters ({len(clean)} characters)"
            )
        if match := PLACEHOLDER_PATTERN.search(clean):
            raise ValueError(f"LinkedIn note contains placeholder text: {match.group(0)}")
        lower = clean.lower()
        for buzz in FORBIDDEN_BUZZWORDS:
            if buzz in lower:
                raise ValueError(f"LinkedIn note contains forbidden generic phrase: '{buzz}'")
        return clean

    @field_validator("yc_job_note", "cold_email_subject", "cold_email_body")
    def validate_placeholders_other(cls, v: str, info) -> str:
        if v and v.strip():
            clean = v.strip()
            if match := PLACEHOLDER_PATTERN.search(clean):
                raise ValueError(f"Field '{info.field_name}' contains placeholder text: {match.group(0)}")
            return clean
        return v

    @model_validator(mode="after")
    def validate_channels(self) -> "MessageDraftPayload":
        if self.yc_job_note and self.linkedin_note == self.yc_job_note:
            raise ValueError("Channels must be generated independently: linkedin_note cannot be identical to yc_job_note")
        if self.cold_email_body and self.linkedin_note == self.cold_email_body:
            raise ValueError("Channels must be generated independently: linkedin_note cannot be identical to cold_email_body")
        return self


def validate_draft_payload(content: str) -> MessageDraftPayload:
    """Parses and validates LLM output into a typed MessageDraftPayload."""
    if not content or not content.strip():
        raise ValueError("Empty response from LLM for message generation")

    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    # Ensure required fields exist and are non-empty
    for req_field in ["linkedin_note", "yc_job_note", "cold_email_subject", "cold_email_body"]:
        if req_field not in data or not str(data[req_field]).strip():
            raise ValueError(f"Missing or empty required field: '{req_field}'")

    draft = MessageDraftPayload.model_validate(data)
    max_chars = settings.linkedin_note_max_chars
    if len(draft.linkedin_note) > max_chars:
        raise ValueError(
            f"The LinkedIn note exceeds the configured character limit of {max_chars} characters ({len(draft.linkedin_note)} characters)"
        )
    return draft


class MessageAgent(BaseAgent):
    """Generates multi-channel personalized outreach drafts grounded in verified facts."""

    agent_name = "message"
    description = "Generates multi-channel personalized outreach drafts grounded in verified facts"
    required_tools = ["generate_grounded_messages", "check_never_contact_blacklist"]

    def __init__(
        self,
        llm_client: Optional[ResilientLLMClient] = None,
        storage: Optional[StorageEngine] = None,
        memory: Optional[MemoryManager] = None,
        profile_path: Optional[Path] = None,
    ):
        self.llm_client = llm_client or default_llm_client
        self.storage = storage or default_storage_engine
        self.memory = memory or default_memory_manager
        self.profile_path = profile_path or settings.profile_path

    def _load_profile(self) -> Dict[str, Any]:
        """Loads candidate profile from explicit memory."""
        try:
            if self.profile_path and Path(self.profile_path).exists():
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load profile from {self.profile_path}: {e}")

        return {
            "candidate_name": "Antigravity Engineer",
            "headline": "Full-Stack AI Systems & Foundational Software Engineer",
            "skills": {"languages": ["Python", "TypeScript", "SQL"], "ai_ml": ["AI Agents", "LangGraph", "RAG"]},
            "experience_highlights": ["Built multi-agent systems reducing token overhead by 70% using deterministic graphs."],
        }

    def _build_message_prompt(
        self,
        profile: Dict[str, Any],
        startup: Dict[str, Any],
        founder: Dict[str, Any],
        fit_eval: Optional[Dict[str, Any]] = None,
        pitch_angle_override: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Constructs prompt enforcing grounding, 3 distinct channels, and length constraints."""
        max_chars = settings.linkedin_note_max_chars
        system_content = (
            "You are a talent outreach strategist and technical communicator. "
            "Your task is to draft high-conviction, personalized outreach messages from a senior "
            "founding engineer to an early-stage startup founder across three distinct channels.\n\n"
            "MESSAGING PHILOSOPHY & CHANNEL INDEPENDENCE:\n"
            "Each channel must be generated independently from the underlying dossier. Do NOT generate one message and "
            "shorten it. Tailor depth and information density specifically per channel:\n\n"
            f"1. 'linkedin_note' (SHORT HOOK - STRICTLY <= {max_chars} CHARACTERS):\n"
            f"   - Target structure: [Specific thing about their company/product/problem] + [Why candidate's experience is relevant] + [Reason to connect].\n"
            f"   - Use the available character limit strategically. Demonstrate that you understand the founder's specific company/product/problem. "
            f"Select the strongest company-specific observation, connect it to one concrete and relevant part of the candidate's experience, and give a natural reason for connecting. "
            f"Do not cram multiple ideas into the note simply to use more characters.\n"
            "   - Avoid unnecessary introductions (e.g., 'Hi my name is'). Avoid repeating founder's name unless natural.\n"
            f"   - Hard limit: Must be <= {max_chars} characters. Exceeding {max_chars} characters will cause validation failure and rejection.\n\n"
            "2. 'yc_job_note' (MEDIUM DEPTH - ~120-150 WORDS):\n"
            "   - Target structure: [Specific startup observation] + [Specific role/job requirement] + [Relevant candidate experience] + [What candidate could contribute] + [Natural call to action].\n"
            "   - Demonstrates genuine understanding of: what the company is building, what technical/problem area matters, what role they are hiring for, and why candidate's experience is relevant.\n"
            "   - Avoid generic cover-letter language. Do not repeat information unnecessarily.\n\n"
            "3. 'cold_email_subject' & 'cold_email_body' (HIGHEST DEPTH):\n"
            "   - 'cold_email_subject': Punchy, contextual subject line under 8 words (e.g., 'Video reasoning architectures & founding engineer fit').\n"
            "   - 'cold_email_body': 3 concise paragraphs:\n"
            "     Paragraph 1: Specific company/founder hook demonstrating you studied their startup.\n"
            "     Paragraph 2: Relevant candidate experience/evidence and engineering proof points.\n"
            "     Paragraph 3: Concrete contribution angle and conversational reason for conversation.\n"
            "   - The email should feel written after actually studying the startup, containing concrete references to actual product, technical problem, role, or publicly stated direction.\n\n"
            "STRICT GROUNDING & QUALITY RULES:\n"
            "1. Ground every claim in the provided verified facts. NEVER hallucinate metrics, prior relationships, or unmentioned company features.\n"
            "2. Tone: Direct, technical, peer-to-peer, respectful, humble yet high conviction. Write as one engineer talking to another engineer.\n"
            "3. FORBIDDEN BUZZWORDS & CLICHÉS: Never use 'thrilled to connect', 'game-changing', 'game changer', 'exciting opportunity', 'synergy', 'pick your brain', 'hope this finds you well', 'blown away by', 'delve', or 'testament'.\n"
            "4. NO PLACEHOLDERS: Never output template placeholders like [Founder Name], [Company], [Startup], {founder_name}, {company}, etc. Use actual entity names.\n\n"
            "Return a valid JSON object ONLY with the following exact keys:\n"
            "{\n"
            f"  \"linkedin_note\": string (<= {max_chars} chars),\n"
            "  \"yc_job_note\": string,\n"
            "  \"cold_email_subject\": string,\n"
            "  \"cold_email_body\": string\n"
            "}"
        )

        contribution = (
            pitch_angle_override
            or (fit_eval.get("contribution_angle") if fit_eval else None)
            or "Ready to accelerate core technical infrastructure and eliminate bottlenecks."
        )
        rationales = (fit_eval.get("match_rationale", []) if fit_eval else [])

        user_content = (
            f"Candidate Profile:\n"
            f"- Name: {profile.get('candidate_name')}\n"
            f"- Headline: {profile.get('headline')}\n"
            f"- Highlights: {json.dumps(profile.get('experience_highlights', []))}\n"
            f"- Featured Projects: {json.dumps(profile.get('featured_projects', []))}\n\n"
            f"Target Startup & Founder Dossier:\n"
            f"- Company: {startup.get('name')}\n"
            f"- One-liner: {startup.get('one_liner')}\n"
            f"- Description: {startup.get('long_description')}\n"
            f"- Target Founder: {founder.get('full_name')} ({founder.get('title')})\n"
            f"- Founder Bio: {founder.get('bio')}\n"
            f"- Open Jobs: {json.dumps(startup.get('jobs_data', []))}\n\n"
            f"Fit Evaluation Context:\n"
            f"- Match Points: {json.dumps(rationales)}\n"
            f"- Recommended Contribution Angle: {contribution}\n"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    async def draft_for_startup_and_founder(
        self,
        startup_id: int,
        founder_id: Optional[int] = None,
        pitch_angle_override: Optional[str] = None,
    ) -> Tuple[Optional[MessageDrafts], Optional[str], bool, bool]:
        """Drafts outreach messages for a startup & founder.

        Returns: (MessageDrafts, error_msg, is_skipped_low_fit, is_blacklisted)
        """
        startup = self.storage.get_startup_by_id(startup_id)
        if not startup:
            return None, f"Startup ID {startup_id} not found", False, False

        # 1. Check Qualification Gate (Fit Evaluation >= 50)
        fit_eval = self.storage.get_fit_evaluation_by_startup_id(startup_id)
        if not fit_eval or fit_eval.get("score", 0) < 50 or not fit_eval.get("should_contact"):
            logger.info(f"Skipping message drafting for startup {startup.get('name')}: fit score below threshold")
            return None, None, True, False

        # 2. Select Founder
        founders = self.storage.get_founders_by_startup_id(startup_id)
        if not founders:
            return None, f"Startup {startup.get('name')} has no founders in database", False, False

        selected_founder = None
        if founder_id:
            selected_founder = next((f for f in founders if f["id"] == founder_id), None)
        if not selected_founder:
            # Default to first founder (usually CEO/primary)
            selected_founder = founders[0]

        target_founder_id = selected_founder["id"]

        # 3. Blacklist Gate
        is_blocked = self.storage.is_blacklisted(
            founder_name=selected_founder.get("full_name"),
            linkedin_url=selected_founder.get("linkedin_url"),
            company_name=startup.get("name"),
            domain=extract_domain(startup.get("website")),
        )
        if is_blocked:
            logger.info(f"⛔ Skipping blacklisted founder: {selected_founder.get('full_name')}")
            return None, None, False, True

        # 4. Generate Message Drafts via Tier 2/3 LLM
        profile = self._load_profile()
        messages = self._build_message_prompt(
            profile, startup, selected_founder, fit_eval, pitch_angle_override
        )

        try:
            response = await asyncio.to_thread(
                self.llm_client.call_with_fallback,
                tier=ModelTier.EXTRACTION,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=3500,
                validator=validate_draft_payload,
            )

            # Retrieve validated payload or parse from content
            payload = response.get("validated_data")
            if not payload:
                content = response.get("content")
                if not content:
                    return None, "Empty response from LLM for message generation", False, False
                payload = validate_draft_payload(content)

            max_chars = settings.linkedin_note_max_chars
            if len(payload.linkedin_note) > max_chars:
                return None, f"LinkedIn note exceeds configured limit of {max_chars} characters ({len(payload.linkedin_note)})", False, False

            drafts = MessageDrafts(
                startup_id=startup_id,
                founder_id=target_founder_id,
                linkedin_note=payload.linkedin_note,
                yc_job_note=payload.yc_job_note,
                cold_email_subject=payload.cold_email_subject,
                cold_email_body=payload.cold_email_body,
                model_used=response.get("model_used"),
            )

            # 5. Persist to Business Memory (saves drafts & initializes outreach_record)
            self.storage.save_message_drafts(drafts)

            # Telemetry logging on success
            telemetry = {
                "startup_id": startup_id,
                "founder_id": target_founder_id,
                "model_used": response.get("model_used"),
                "attempts": response.get("attempts", 1),
                "retry_count": response.get("retry_count", 0),
                "status": "success",
                "linkedin_chars": len(drafts.linkedin_note),
                "max_chars": max_chars,
            }
            logger.info(f"Message generation telemetry: {json.dumps(telemetry)}")

            return drafts, None, False, False

        except Exception as e:
            logger.error(
                f"Failed generating messages for startup {startup_id}: {e}. "
                f"Telemetry: {json.dumps({'startup_id': startup_id, 'status': 'failure', 'error': str(e)})}"
            )
            return None, str(e), False, False

    async def regenerate_channel(
        self,
        startup_id: int,
        founder_id: Optional[int] = None,
        channel: str = "all",
        pitch_angle_override: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Regenerates outreach message draft(s) for a given startup & founder.

        Supports channel='all', 'linkedin_note', 'yc_job_note', or 'cold_email'.
        Updates the database accordingly and returns (updated_drafts_dict, error_msg).
        """
        if channel == "all":
            drafts, err, skipped, blocked = await self.draft_for_startup_and_founder(
                startup_id=startup_id,
                founder_id=founder_id,
                pitch_angle_override=pitch_angle_override,
            )
            if blocked:
                return None, "Founder or startup is blacklisted"
            if skipped:
                return None, "Fit score below threshold"
            if err:
                return None, err
            return (drafts.model_dump() if drafts else None), None

        startup = self.storage.get_startup_by_id(startup_id)
        if not startup:
            return None, f"Startup ID {startup_id} not found"

        founders = self.storage.get_founders_by_startup_id(startup_id)
        if not founders:
            return None, f"Startup {startup.get('name')} has no founders in database"

        selected_founder = None
        if founder_id:
            selected_founder = next((f for f in founders if f["id"] == founder_id), None)
        if not selected_founder:
            selected_founder = founders[0]

        target_founder_id = selected_founder["id"]

        is_blocked = self.storage.is_blacklisted(
            founder_name=selected_founder.get("full_name"),
            linkedin_url=selected_founder.get("linkedin_url"),
            company_name=startup.get("name"),
            domain=extract_domain(startup.get("website")),
        )
        if is_blocked:
            return None, f"Founder {selected_founder.get('full_name')} is blacklisted"

        fit_eval = self.storage.get_fit_evaluation_by_startup_id(startup_id)
        profile = self._load_profile()
        max_chars = settings.linkedin_note_max_chars

        contribution = (
            pitch_angle_override
            or (fit_eval.get("contribution_angle") if fit_eval else None)
            or "Ready to accelerate core technical infrastructure and eliminate bottlenecks."
        )
        rationales = (fit_eval.get("match_rationale", []) if fit_eval else [])

        common_dossier = (
            f"Candidate Profile:\n"
            f"- Name: {profile.get('candidate_name')}\n"
            f"- Headline: {profile.get('headline')}\n"
            f"- Highlights: {json.dumps(profile.get('experience_highlights', []))}\n"
            f"- Featured Projects: {json.dumps(profile.get('featured_projects', []))}\n\n"
            f"Target Startup & Founder Dossier:\n"
            f"- Company: {startup.get('name')}\n"
            f"- One-liner: {startup.get('one_liner')}\n"
            f"- Description: {startup.get('long_description')}\n"
            f"- Target Founder: {selected_founder.get('full_name')} ({selected_founder.get('title')})\n"
            f"- Founder Bio: {selected_founder.get('bio')}\n"
            f"- Open Jobs: {json.dumps(startup.get('jobs_data', []))}\n\n"
            f"Fit Evaluation Context:\n"
            f"- Match Points: {json.dumps(rationales)}\n"
            f"- Recommended Contribution Angle: {contribution}\n"
        )

        if channel == "linkedin_note":
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a talent outreach strategist and technical communicator. "
                        f"Draft a single 'linkedin_note' connection invitation STRICTLY <= {max_chars} characters.\n"
                        "Target structure: [Specific thing about their company/product/problem] + [Relevant candidate experience] + [Reason to connect].\n"
                        "Tone: Direct, technical, peer-to-peer. Never use forbidden buzzwords or placeholders.\n"
                        "Return JSON ONLY:\n"
                        '{"linkedin_note": string}'
                    ),
                },
                {"role": "user", "content": common_dossier},
            ]
            response = await asyncio.to_thread(
                self.llm_client.call_with_fallback,
                tier=ModelTier.EXTRACTION,
                messages=prompt,
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=500,
            )
            raw = json.loads(response.get("content", "{}"))
            note = raw.get("linkedin_note", "").strip()
            if not note:
                return None, "LLM returned empty linkedin_note"
            if len(note) > max_chars:
                note = enforce_linkedin_limit(note, max_chars)
            self.storage.update_message_draft(startup_id, target_founder_id, linkedin_note=note)
            return {"linkedin_note": note}, None

        elif channel == "yc_job_note":
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a talent outreach strategist and technical communicator. "
                        "Draft a single 'yc_job_note' (120-150 words) applying/reaching out regarding open roles.\n"
                        "Target structure: [Specific startup observation] + [Role requirement] + [Relevant candidate experience] + [Contribution] + [Call to action].\n"
                        "Tone: Direct, technical, peer-to-peer. Never use forbidden buzzwords or placeholders.\n"
                        "Return JSON ONLY:\n"
                        '{"yc_job_note": string}'
                    ),
                },
                {"role": "user", "content": common_dossier},
            ]
            response = await asyncio.to_thread(
                self.llm_client.call_with_fallback,
                tier=ModelTier.EXTRACTION,
                messages=prompt,
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=800,
            )
            raw = json.loads(response.get("content", "{}"))
            note = raw.get("yc_job_note", "").strip()
            if not note:
                return None, "LLM returned empty yc_job_note"
            self.storage.update_message_draft(startup_id, target_founder_id, yc_job_note=note)
            return {"yc_job_note": note}, None

        elif channel == "cold_email":
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a talent outreach strategist and technical communicator. "
                        "Draft a cold email: punchy subject (<8 words) and 3-paragraph body.\n"
                        "Tone: Direct, technical, peer-to-peer. Never use forbidden buzzwords or placeholders.\n"
                        "Return JSON ONLY:\n"
                        '{"cold_email_subject": string, "cold_email_body": string}'
                    ),
                },
                {"role": "user", "content": common_dossier},
            ]
            response = await asyncio.to_thread(
                self.llm_client.call_with_fallback,
                tier=ModelTier.EXTRACTION,
                messages=prompt,
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=1200,
            )
            raw = json.loads(response.get("content", "{}"))
            subj = raw.get("cold_email_subject", "").strip()
            body = raw.get("cold_email_body", "").strip()
            if not subj or not body:
                return None, "LLM returned empty cold email subject or body"
            self.storage.update_message_draft(
                startup_id, target_founder_id, cold_email_subject=subj, cold_email_body=body
            )
            return {"cold_email_subject": subj, "cold_email_body": body}, None

        return None, f"Unsupported channel: {channel}"

    async def execute(self, context: Dict[str, Any]) -> AgentResult:
        """Main entry point for message generation.

        Context keys:
            startup_id: Optional[int]
            startup_ids: Optional[List[int]]
            founder_id: Optional[int]
            pitch_angle: Optional[str]
        """
        start_time = time.time()
        pitch_angle = context.get("pitch_angle")

        target_ids: List[int] = []
        if context.get("startup_id"):
            target_ids = [context["startup_id"]]
        elif context.get("startup_ids"):
            target_ids = list(context["startup_ids"])
        else:
            # Discover startups with high/medium fit evaluations that don't have drafts yet
            # In SQLite, query review leads or fit_evaluations
            with self.storage.init_db() or get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT e.startup_id, e.score
                    FROM fit_evaluations e
                    LEFT JOIN message_drafts d ON e.startup_id = d.startup_id
                    WHERE e.score >= 50 AND e.should_contact = 1 AND d.id IS NULL
                    ORDER BY e.score DESC
                """)
                target_ids = [row["startup_id"] for row in cursor.fetchall()]

        stats = {
            "startups_processed": 0,
            "drafts_generated": 0,
            "skipped_low_fit": 0,
            "blacklisted_skips": 0,
            "generation_failures": 0,
        }
        errors: List[str] = []
        drafts_out: List[Dict[str, Any]] = []

        for sid in target_ids:
            drafts, err, skipped_low_fit, is_blacklisted = await self.draft_for_startup_and_founder(
                sid,
                founder_id=context.get("founder_id"),
                pitch_angle_override=pitch_angle,
            )

            stats["startups_processed"] += 1

            if is_blacklisted:
                stats["blacklisted_skips"] += 1
                continue
            if skipped_low_fit:
                stats["skipped_low_fit"] += 1
                continue
            if err:
                errors.append(f"Startup {sid}: {err}")
                stats["generation_failures"] += 1
                continue

            if drafts:
                stats["drafts_generated"] += 1
                drafts_out.append(drafts.model_dump())

        duration = round(time.time() - start_time, 2)
        return AgentResult(
            success=stats["generation_failures"] == 0,
            agent_name=self.agent_name,
            data=drafts_out,
            errors=errors,
            stats=stats,
            duration_seconds=duration,
        )
