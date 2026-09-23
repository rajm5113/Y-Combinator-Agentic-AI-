"""Candidate Matching & Fit Qualification Agent (Spec 3.1).

Evaluates candidate profile alignment against a startup's product, technical stack,
and active hiring needs using Tier 1 Deep Reasoning models.
Assigns objective 0-100 scores, fit tiers (HIGH/MEDIUM/LOW), and specific contribution angles.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agents.base import AgentResult, BaseAgent
from agents.founder_agent import extract_domain
from config.llm_client import ModelTier, ResilientLLMClient, llm_client as default_llm_client
from config.settings import settings
from db.memory import MemoryManager, memory_manager as default_memory_manager
from db.models import FitEvaluation
from db.storage import StorageEngine, storage_engine as default_storage_engine

logger = logging.getLogger("fit_agent")


class FitAgent(BaseAgent):
    """Evaluates startup relevance against candidate explicit profile."""

    agent_name = "fit"
    description = "Evaluates candidate alignment against startups to produce 0-100 scores and contribution angles"
    required_tools = ["evaluate_startup_fit"]
    CACHE_TTL = 604800  # 7 days (evaluation is stable)

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

        # Fallback profile summary
        return {
            "candidate_name": "Founding AI Engineer",
            "headline": "Full-Stack AI Systems & Foundational Software Engineer",
            "skills": {"languages": ["Python", "TypeScript", "SQL"], "ai_ml": ["AI Agents", "LangGraph", "RAG"]},
            "target_roles": ["Founding Engineer", "Lead AI Systems Engineer"],
            "target_startup_criteria": {"preferred_stages": ["Pre-Seed", "Seed", "Series A"], "preferred_team_size_max": 25},
        }

    def _build_evaluation_prompt(
        self, profile: Dict[str, Any], startup: Dict[str, Any], founders: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Construct an evidence-based fit rubric from the canonical candidate profile."""
        scoring = profile.get("scoring_framework", {})
        dimensions = scoring.get("dimensions", [])
        thresholds = profile.get("scoring_thresholds", {})
        career_focus = profile.get("career_focus", {})
        capability_map = profile.get("capability_map", {})
        current_experience = profile.get("current_experience", {})
        startup_preferences = profile.get("startup_preferences", {})

        dimension_lines = []
        for dim in dimensions:
            dimension_lines.append(
                f"- {dim.get('name')}: {dim.get('weight', 0)} points — {dim.get('description', '')}"
            )

        capability_lines = []
        for area, details in capability_map.items():
            skills = ", ".join(details.get("demonstrated_skills", []))
            if skills:
                capability_lines.append(
                    f"- {area} ({details.get('priority', 'secondary')}, {details.get('evidence_level', 'unknown')} evidence): {skills}"
                )

        system_content = (
            "You are a talent-fit evaluation engine for startup outreach. "
            "Evaluate how strongly a startup aligns with this specific candidate's interests, "
            "demonstrated capabilities, current experience, target roles, and preferred problem spaces. "
            "This is a candidate-to-startup fit score, not a judgement of whether the startup is good.\n\n"
            "SCORING FRAMEWORK (100 points total):\n"
            + "\n".join(dimension_lines)
            + f"\nThresholds: HIGH >= {thresholds.get('high', 75)}, "
              f"MEDIUM >= {thresholds.get('medium', 50)}, "
              f"LOW < {thresholds.get('medium', 50)}. "
              f"should_contact is true at >= {thresholds.get('should_contact_from', 50)}.\n\n"
            "Important scoring rules:\n"
            "- Internally allocate integer points to each dimension and make the final score equal their sum. "
            "Do not invent facts to justify points.\n"
            "- Agentic AI / AI-powered analytics is a primary interest.\n"
            "- Data Analytics / Business Intelligence is also a primary interest.\n"
            "- Business Analysis / RevOps alignment is a primary interest supported by current Payoneer experience.\n"
            "- Demonstrated skills and current work evidence should carry more weight than merely aspirational skills.\n"
            "- Do not require an exact job title when the underlying problem space and contribution opportunity align.\n"
            "- Missing public hiring information should not heavily penalize a startup when its product/problem space is strongly aligned.\n"
            "- Use only facts present in the startup data, jobs data, founder context, and candidate profile.\n"
            "- Return valid JSON only with the exact keys: score, fit_tier, should_contact, match_rationale, contribution_angle.\n"
            "- match_rationale must contain 2-4 concise evidence-based reasons.\n"
            "- contribution_angle must describe one concrete way the candidate could contribute.\n"
        )

        founder_summaries = []
        for f in founders:
            name = f.get("full_name")
            title = f.get("title") or "Founder"
            bio = f.get("bio") or "No bio available"
            founder_summaries.append(f"- {name} ({title}): {bio[:200]}")

        user_content = (
            "CANDIDATE PROFILE\n"
            f"- Name: {profile.get('candidate', {}).get('name', profile.get('candidate_name'))}\n"
            f"- Current role: {current_experience.get('role') or profile.get('candidate', {}).get('current_role')}\n"
            f"- Primary interests: {json.dumps(career_focus.get('primary_interest_areas', []))}\n"
            f"- Secondary interests: {json.dumps(career_focus.get('secondary_interest_areas', []))}\n"
            f"- Target roles: {json.dumps(career_focus.get('target_roles', profile.get('target_roles', [])))}\n"
            f"- Preferred problem spaces: {json.dumps(career_focus.get('preferred_problem_spaces', []))}\n"
            f"- Current experience evidence: {json.dumps(current_experience.get('evidence', []))}\n"
            "Demonstrated capability map:\n"
            + "\n".join(capability_lines)
            + "\nPreferred startup characteristics:\n"
            f"- Industries: {json.dumps(startup_preferences.get('preferred_industries', []))}\n"
            f"- Stages: {json.dumps(startup_preferences.get('preferred_stages', []))}\n"
            f"- Team size preference: <= {startup_preferences.get('preferred_team_size_max', 25)}\n"
            f"- Environment: {json.dumps(startup_preferences.get('environment_preferences', []))}\n\n"
            "STARTUP INFORMATION\n"
            f"- Name: {startup.get('name')}\n"
            f"- One Liner: {startup.get('one_liner')}\n"
            f"- Description: {startup.get('long_description')}\n"
            f"- Industry / Tags: {startup.get('industry')}, {startup.get('tags')}\n"
            f"- Team Size: {startup.get('team_size')}\n"
            f"- Hiring Status: {'Hiring' if startup.get('is_hiring') else 'Not explicitly hiring'}\n"
            f"- Jobs: {json.dumps(startup.get('jobs_data', []))}\n"
            f"- Founders:\n" + "\n".join(founder_summaries or ["No founder details"])
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    async def evaluate_startup(
        self, startup_id: int, force_refresh: bool = False
    ) -> Tuple[Optional[FitEvaluation], Optional[str], bool, bool]:
        """Evaluates a single startup.

        Returns: (FitEvaluation, error_msg, is_cached, is_blacklisted)
        """
        startup = self.storage.get_startup_by_id(startup_id)
        if not startup:
            return None, f"Startup ID {startup_id} not found", False, False

        # 1. Blacklist Gate (Input Guardrail)
        is_blocked = self.storage.is_blacklisted(
            company_name=startup.get("name"),
            domain=extract_domain(startup.get("website")),
        )
        if is_blocked:
            logger.info(f"⛔ Skipping blacklisted startup during fit check: {startup.get('name')}")
            return None, None, False, True

        # 2. Cache Check (Tier 2 Memory)
        if not force_refresh:
            cached = self.memory.get_cached_tool_result("evaluate_startup_fit", {"startup_id": startup_id})
            if cached:
                try:
                    fit_eval = FitEvaluation(**cached)
                    return fit_eval, None, True, False
                except Exception:
                    pass

        # 3. Load Context & Construct Prompt
        profile = self._load_profile()
        founders = self.storage.get_founders_by_startup_id(startup_id)
        messages = self._build_evaluation_prompt(profile, startup, founders)

        # 4. Invoke LLM via Tier 1 Reasoning
        try:
            response = await asyncio.to_thread(
                self.llm_client.call_with_fallback,
                tier=ModelTier.REASONING,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=2500,
            )
            content = response.get("content")
            if not content:
                return None, f"Empty response from LLM for startup {startup.get('name')}", False, False

            data = json.loads(content)
            raw_score = int(data.get("score", 0))
            score = max(0, min(100, raw_score))

            if score >= 75:
                tier = "HIGH"
                should_contact = True
            elif score >= 50:
                tier = "MEDIUM"
                should_contact = True
            else:
                tier = "LOW"
                should_contact = False

            fit_eval = FitEvaluation(
                startup_id=startup_id,
                score=score,
                fit_tier=tier,
                should_contact=should_contact,
                match_rationale=data.get("match_rationale") or [],
                contribution_angle=data.get("contribution_angle"),
                model_used=response.get("model_used"),
            )

            # 5. Persist to Business Memory
            self.storage.save_fit_evaluation(fit_eval)

            # 6. Cache in Cache Memory
            self.memory.cache_tool_result(
                "evaluate_startup_fit",
                {"startup_id": startup_id},
                fit_eval.model_dump(),
                ttl=self.CACHE_TTL,
            )

            return fit_eval, None, False, False

        except Exception as e:
            logger.error(f"Error evaluating startup {startup_id}: {e}")
            return None, str(e), False, False

    async def execute(self, context: Dict[str, Any]) -> AgentResult:
        """Main entry point for fit evaluation.

        Context keys:
            startup_id: Optional[int]
            startup_ids: Optional[List[int]]
            startup_slug: Optional[str]
            force_refresh: bool (default: False)
            min_score: int (default: 50)
        """
        start_time = time.time()
        force_refresh = context.get("force_refresh", False)

        target_ids: List[int] = []

        if context.get("startup_id"):
            target_ids = [context["startup_id"]]
        elif context.get("startup_ids"):
            target_ids = list(context["startup_ids"])
        elif context.get("startup_slug"):
            st = self.storage.get_startup_by_slug(context["startup_slug"])
            if st:
                target_ids = [st["id"]]
        else:
            # Auto-discovery of unevaluated startups
            unevaluated = self.storage.get_startups_without_fit_evaluation()
            target_ids = [s["id"] for s in unevaluated]

        stats = {
            "startups_evaluated": 0,
            "high_fit_count": 0,
            "medium_fit_count": 0,
            "low_fit_count": 0,
            "blacklisted_skips": 0,
            "cached_hits": 0,
            "evaluation_failures": 0,
        }
        errors: List[str] = []
        evaluations_out: List[Dict[str, Any]] = []

        for sid in target_ids:
            fit_eval, error_msg, is_cached, is_blacklisted = await self.evaluate_startup(
                sid, force_refresh=force_refresh
            )

            if is_blacklisted:
                stats["blacklisted_skips"] += 1
                continue

            if error_msg:
                errors.append(f"Startup {sid}: {error_msg}")
                stats["evaluation_failures"] += 1
                continue

            if is_cached:
                stats["cached_hits"] += 1

            if fit_eval:
                stats["startups_evaluated"] += 1
                if fit_eval.fit_tier == "HIGH":
                    stats["high_fit_count"] += 1
                elif fit_eval.fit_tier == "MEDIUM":
                    stats["medium_fit_count"] += 1
                else:
                    stats["low_fit_count"] += 1

                evaluations_out.append(fit_eval.model_dump())

        duration = round(time.time() - start_time, 2)
        return AgentResult(
            success=stats["evaluation_failures"] == 0,
            agent_name=self.agent_name,
            data=evaluations_out,
            errors=errors,
            stats=stats,
            duration_seconds=duration,
        )
