"""Master Pipeline Orchestrator for the YC Founder Outreach system.

Executes a deterministic DAG pipeline:
    Discovery -> Founder Extraction -> Fit Evaluation -> [Qualification Gate] -> Message Drafting

All orchestration logic is deterministic code (Plan-and-Solve) — zero LLM tokens are burned
on routing or coordination. LLM calls happen exclusively within specialist agents.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from agents.discovery_agent import DiscoveryAgent
from agents.fit_agent import FitAgent
from agents.founder_agent import FounderAgent
from agents.message_agent import MessageAgent
from config.settings import settings
from location_utils import extract_job_locations, locations_match
from db.memory import MemoryManager, memory_manager as default_memory_manager
from db.storage import StorageEngine, storage_engine as default_storage_engine

logger = logging.getLogger("pipeline_orchestrator")


class PipelineStage(str, Enum):
    """Execution stages of the outreach pipeline."""
    DISCOVERY = "discovery"
    FOUNDER = "founder"
    FIT = "fit"
    MESSAGE = "message"


class PipelineConfig(BaseModel):
    """Validated configuration for pipeline execution."""
    batches: List[str] = Field(
        default_factory=lambda: list(settings.default_batches),
        description="YC batches to scan (e.g., ['Fall 2026', 'Summer 2026'])",
    )
    industries: List[str] = Field(
        default_factory=list,
        description="Industry filter tags (empty list = scan all industries)",
    )
    limit: Optional[int] = Field(
        default=None, ge=1, le=500,
        description="Maximum startups to process per batch (None = all)",
    )
    min_fit_score: int = Field(
        default=settings.pipeline_default_min_score, ge=0, le=100,
        description="Minimum fit score required to pass qualification gate",
    )
    target_country: str = Field(
        default_factory=lambda: settings.target_country,
        description="Country in which the candidate wants current offices or active jobs.",
    )
    target_city: str = Field(
        default_factory=lambda: settings.target_city,
        description="Optional city restriction inside the target country.",
    )
    target_location_mode: str = Field(
        default_factory=lambda: settings.target_location_mode,
        description="Location evidence to accept: office_or_job, office_only, or job_only.",
    )
    target_location_unknown_policy: str = Field(
        default_factory=lambda: settings.target_location_unknown_policy,
        description="How to handle records with no reliable location evidence.",
    )
    max_concurrency: int = Field(
        default=settings.pipeline_max_concurrency, ge=1, le=20,
        description="Bounded parallelism limit for concurrent agent executions",
    )
    skip_stages: List[PipelineStage] = Field(
        default_factory=list,
        description="Stages to skip (e.g., [PipelineStage.DISCOVERY] to resume from DB)",
    )
    force_refresh: bool = Field(
        default=False,
        description="Bypass cached tool results and re-evaluate",
    )
    dry_run: bool = Field(
        default=False,
        description="Execute discovery only; skips founder extraction and all LLM calls",
    )


class StageResult(BaseModel):
    """Execution metrics and status for a single pipeline stage."""
    stage: PipelineStage
    success: bool
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0
    errors: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class PipelineReport(BaseModel):
    """Typed summary contract returned after pipeline execution."""
    session_id: str
    config: PipelineConfig
    stages: Dict[PipelineStage, StageResult] = Field(default_factory=dict)
    total_startups_discovered: int = 0
    total_founders_extracted: int = 0
    total_fit_evaluated: int = 0
    total_location_matched: int = 0
    total_location_filtered: int = 0
    total_location_unknown: int = 0
    total_qualified: int = 0
    total_drafts_generated: int = 0
    total_duration_seconds: float = 0.0
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool = False


class MasterOrchestrator:
    """Deterministic DAG executor welding specialist agents into an end-to-end pipeline.

    Orchestration Pattern: Plan-and-Solve (pure code executor, zero tokens on coordination).
    Concurrency: Bounded parallelism via asyncio.Semaphore to respect OpenRouter rate limits.
    Error Handling: Fail-soft containment per stage item; errors logged and aggregated.
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        storage: Optional[StorageEngine] = None,
        memory: Optional[MemoryManager] = None,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.config = config or PipelineConfig()
        self.storage = storage or default_storage_engine
        self.memory = memory or default_memory_manager
        self.progress_callback = progress_callback
        self.session_id = self._generate_session_id()
        self.cancel_event = asyncio.Event()

    def cancel(self) -> None:
        """Signals the pipeline execution to stop gracefully."""
        self.cancel_event.set()
        logger.info(f"Cancellation requested for pipeline session {self.session_id}")

    @staticmethod
    def _generate_session_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:6]
        return f"pipe_{timestamp}_{uid}"

    def _notify_progress(self, event: str, payload: Dict[str, Any]) -> None:
        """Notifies progress callback, updates ephemeral Redis session state, and DB progress."""
        try:
            self.memory.set_session_state(
                self.session_id, event, payload, ttl=settings.pipeline_session_ttl
            )
        except Exception as e:
            logger.debug(f"Failed to write session state: {e}")

        # Update DB progress message if relevant
        try:
            msg = payload.get("message") or f"{event}: {payload.get('stage', '')}"
            self.storage.update_pipeline_run_progress(self.session_id, msg)
        except Exception as e:
            logger.debug(f"Failed to update db pipeline progress: {e}")

        if self.progress_callback:
            try:
                self.progress_callback(event, payload)
            except Exception as e:
                logger.warning(f"Error in progress callback: {e}")

    async def _run_bounded_parallel(
        self,
        coro_factories: List[Callable[[], Any]],
        concurrency: int,
    ) -> Tuple[List[Any], List[str]]:
        """Executes a list of coroutine factories with bounded parallelism via semaphore.

        Using coroutine factories (callables that return coroutines) ensures that coroutines
        are only instantiated when their worker starts, avoiding pending coroutine leaks.
        """
        semaphore = asyncio.Semaphore(concurrency)
        successes: List[Any] = []
        failures: List[str] = []
        lock = asyncio.Lock()

        async def worker(factory: Callable[[], Any]):
            if self.cancel_event.is_set():
                return
            async with semaphore:
                if self.cancel_event.is_set():
                    return
                try:
                    res = await factory()
                    async with lock:
                        successes.append(res)
                except Exception as exc:
                    err = str(exc)
                    logger.warning(f"Parallel worker failure: {err}")
                    async with lock:
                        failures.append(err)

        tasks = [worker(factory) for factory in coro_factories]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return successes, failures

    async def _run_stage_discovery(self, report: PipelineReport) -> List[Dict[str, Any]]:
        """Stage 1: Discover startups via Algolia / YC-OSS fallback."""
        start_t = time.monotonic()
        self._notify_progress("stage_start", {"stage": PipelineStage.DISCOVERY.value})

        agent = DiscoveryAgent()
        context = {
            "batches": self.config.batches,
            "industries": self.config.industries,
            "limit": self.config.limit,
            "force_refresh": self.config.force_refresh,
        }
        res = await agent.run(context)
        dur = time.monotonic() - start_t

        startups: List[Dict[str, Any]] = res.data if (res.success and isinstance(res.data, list)) else []

        stage_res = StageResult(
            stage=PipelineStage.DISCOVERY,
            success=res.success and len(startups) > 0,
            items_processed=len(startups),
            items_succeeded=len(startups),
            items_failed=len(res.errors),
            errors=res.errors,
            duration_seconds=dur,
        )
        report.stages[PipelineStage.DISCOVERY] = stage_res
        report.total_startups_discovered = len(startups)

        self._notify_progress("stage_complete", {
            "stage": PipelineStage.DISCOVERY.value,
            "success": stage_res.success,
            "discovered": len(startups),
        })
        return startups

    async def _run_stage_founders(
        self,
        startups: List[Dict[str, Any]],
        report: PipelineReport,
    ) -> List[int]:
        """Stage 2: Extract founders with bounded parallelism."""
        start_t = time.monotonic()
        self._notify_progress("stage_start", {"stage": PipelineStage.FOUNDER.value})

        # Determine slugs that need extraction
        slugs_to_process: List[str] = []
        for s in startups:
            slug = s.get("slug")
            if not slug:
                continue
            # Check if startup already has founders in DB unless force_refresh is on
            if not self.config.force_refresh:
                st_id = s.get("startup_id") or s.get("id")
                if st_id:
                    existing = self.storage.get_founders_by_startup_id(st_id)
                    if existing:
                        continue
            slugs_to_process.append(slug)

        # If no slugs from discovered list need processing, check DB
        if not slugs_to_process and not startups:
            needing = self.storage.get_startups_needing_founders()
            slugs_to_process = [s["slug"] for s in needing if s.get("slug")]

        agent = FounderAgent()
        all_errors: List[str] = []
        succeeded_count = 0
        failed_count = 0

        # Build factory list for bounded parallel execution
        def make_factory(slug: str):
            async def run_one():
                res = await agent.run({
                    "startup_slugs": [slug],
                    "force_refresh": self.config.force_refresh,
                })
                if not res.success or res.errors:
                    for err in res.errors:
                        all_errors.append(f"Founder extraction [{slug}]: {err}")
                if res.success:
                    return slug
                raise RuntimeError(f"Founder extraction failed for {slug}")
            return run_one

        factories = [make_factory(slug) for slug in slugs_to_process]
        successes, failures = await self._run_bounded_parallel(
            factories, concurrency=self.config.max_concurrency
        )
        succeeded_count = len(successes)
        failed_count = len(failures)
        all_errors.extend(failures)

        dur = time.monotonic() - start_t
        items_total = len(slugs_to_process)
        stage_success = (items_total == 0) or (succeeded_count > 0)

        # Calculate total founders extracted
        total_founders = 0
        all_startup_ids: List[int] = []
        for s in startups:
            sid = s.get("startup_id") or s.get("id")
            if sid:
                all_startup_ids.append(sid)
                founders = self.storage.get_founders_by_startup_id(sid)
                total_founders += len(founders)

        stage_res = StageResult(
            stage=PipelineStage.FOUNDER,
            success=stage_success,
            items_processed=items_total,
            items_succeeded=succeeded_count,
            items_failed=failed_count,
            errors=all_errors,
            duration_seconds=dur,
        )
        report.stages[PipelineStage.FOUNDER] = stage_res
        report.total_founders_extracted = total_founders

        self._notify_progress("stage_complete", {
            "stage": PipelineStage.FOUNDER.value,
            "success": stage_success,
            "founders_total": total_founders,
        })
        return all_startup_ids

    def _filter_by_target_location(self, startup_ids: List[int], report: PipelineReport) -> List[int]:
        """Deterministic geography gate: only pass India/city-matching startups to Fit."""
        matched: List[int] = []
        filtered = 0
        unknown = 0

        for sid in startup_ids:
            startup = self.storage.get_startup_by_id(sid)
            if not startup:
                filtered += 1
                continue

            try:
                office_locations = json.loads(startup.get("office_locations") or "[]")
            except Exception:
                office_locations = []

            try:
                jobs = json.loads(startup.get("jobs_data") or "[]")
            except Exception:
                jobs = []

            job_locations = extract_job_locations(jobs)
            if self.config.target_location_mode == "office_only":
                job_locations = []
            elif self.config.target_location_mode == "job_only":
                office_locations = []

            match = locations_match(
                office_locations=office_locations,
                job_locations=job_locations,
                target_country=self.config.target_country,
                target_city=self.config.target_city,
            )

            if match == "MATCH":
                matched.append(sid)
            elif match == "UNKNOWN" and self.config.target_location_unknown_policy == "include":
                matched.append(sid)
            elif match == "UNKNOWN":
                unknown += 1
            else:
                filtered += 1

        report.total_location_matched = len(matched)
        report.total_location_filtered = filtered
        report.total_location_unknown = unknown

        logger.info(
            f"[Location Gate] target={self.config.target_country or 'Any'}"
            f"/{self.config.target_city or 'All cities'} "
            f"matched={len(matched)} filtered={filtered} unknown={unknown}"
        )
        return matched

    async def _run_stage_fit(
        self,
        startup_ids: List[int],
        report: PipelineReport,
    ) -> List[int]:
        """Stage 3: Evaluate startup fit with bounded parallelism."""
        start_t = time.monotonic()
        self._notify_progress("stage_start", {"stage": PipelineStage.FIT.value})

        target_ids = list(startup_ids)
        if not self.config.force_refresh:
            target_ids = [
                sid for sid in target_ids
                if not self.storage.get_fit_evaluation_by_startup_id(sid)
            ]
        if not target_ids:
            unevaluated = self.storage.get_startups_without_fit_evaluation()
            target_ids = [s["id"] for s in unevaluated]

        agent = FitAgent()
        all_errors: List[str] = []

        def make_factory(sid: int):
            async def run_one():
                res = await agent.run({
                    "startup_id": sid,
                    "force_refresh": self.config.force_refresh,
                    "min_score": self.config.min_fit_score,
                })
                if not res.success or res.errors:
                    for err in res.errors:
                        all_errors.append(f"Fit evaluation [id={sid}]: {err}")
                if res.success:
                    return sid
                raise RuntimeError(f"Fit evaluation failed for startup {sid}")
            return run_one

        factories = [make_factory(sid) for sid in target_ids]
        successes, failures = await self._run_bounded_parallel(
            factories, concurrency=self.config.max_concurrency
        )
        all_errors.extend(failures)

        dur = time.monotonic() - start_t
        items_total = len(target_ids)
        succeeded_count = len(successes)
        failed_count = len(failures)
        stage_success = (items_total == 0) or (succeeded_count > 0)

        stage_res = StageResult(
            stage=PipelineStage.FIT,
            success=stage_success,
            items_processed=items_total,
            items_succeeded=succeeded_count,
            items_failed=failed_count,
            errors=all_errors,
            duration_seconds=dur,
        )
        report.stages[PipelineStage.FIT] = stage_res
        report.total_fit_evaluated = succeeded_count

        self._notify_progress("stage_complete", {
            "stage": PipelineStage.FIT.value,
            "success": stage_success,
            "evaluated": succeeded_count,
        })
        return successes

    async def _run_stage_messages(
        self,
        qualified_ids: List[int],
        report: PipelineReport,
    ) -> int:
        """Stage 4: Generate tailored message drafts with bounded parallelism."""
        start_t = time.monotonic()
        self._notify_progress("stage_start", {"stage": PipelineStage.MESSAGE.value})

        agent = MessageAgent()
        all_errors: List[str] = []

        def make_factory(sid: int):
            async def run_one():
                res = await agent.run({
                    "startup_id": sid,
                    "force_refresh": self.config.force_refresh,
                })
                if not res.success or res.errors:
                    for err in res.errors:
                        all_errors.append(f"Message generation [id={sid}]: {err}")
                if res.success:
                    return sid
                raise RuntimeError(f"Message generation failed for startup {sid}")
            return run_one

        factories = [make_factory(sid) for sid in qualified_ids]
        successes, failures = await self._run_bounded_parallel(
            factories, concurrency=self.config.max_concurrency
        )
        all_errors.extend(failures)

        dur = time.monotonic() - start_t
        items_total = len(qualified_ids)
        succeeded_count = len(successes)
        failed_count = len(failures)
        stage_success = (items_total == 0) or (succeeded_count > 0)

        stage_res = StageResult(
            stage=PipelineStage.MESSAGE,
            success=stage_success,
            items_processed=items_total,
            items_succeeded=succeeded_count,
            items_failed=failed_count,
            errors=all_errors,
            duration_seconds=dur,
        )
        report.stages[PipelineStage.MESSAGE] = stage_res
        report.total_drafts_generated = succeeded_count

        self._notify_progress("stage_complete", {
            "stage": PipelineStage.MESSAGE.value,
            "success": stage_success,
            "drafts_generated": succeeded_count,
        })
        return succeeded_count

    async def run(self) -> PipelineReport:
        """Executes the complete deterministic Plan-and-Solve outreach pipeline.

        Returns:
            PipelineReport containing metrics, timestamps, and per-stage results.
        """
        start_time = datetime.now(timezone.utc)
        report = PipelineReport(
            session_id=self.session_id,
            config=self.config,
            started_at=start_time,
        )

        logger.info(
            f"🚀 [Pipeline] Starting run session={self.session_id} | "
            f"batches={self.config.batches} | target={self.config.target_country or 'Any'}"
            f"/{self.config.target_city or 'All cities'} | "
            f"min_score={self.config.min_fit_score} | concurrency={self.config.max_concurrency}"
        )

        try:
            # Record run start in durable DB
            try:
                self.storage.record_pipeline_run_start(
                    session_id=self.session_id,
                    batch=self.config.batches[0] if self.config.batches else "",
                    industry=self.config.industries[0] if self.config.industries else None,
                    target_country=self.config.target_country,
                    target_city=self.config.target_city,
                    target_location_mode=self.config.target_location_mode,
                    startup_limit=self.config.limit or 0,
                    min_fit_score=self.config.min_fit_score,
                    max_concurrency=self.config.max_concurrency,
                    dry_run=self.config.dry_run,
                )
            except Exception as e:
                logger.warning(f"Failed to record pipeline run start: {e}")

            # Initialize session context in memory engine
            self.memory.set_session_state(
                self.session_id, "status", "running", ttl=settings.pipeline_session_ttl
            )
            self.memory.set_session_state(
                self.session_id, "config", self.config.model_dump(), ttl=settings.pipeline_session_ttl
            )
            self.memory.set_session_state(
                self.session_id, "started_at", start_time.isoformat(), ttl=settings.pipeline_session_ttl
            )

            # ── Stage 1: Discovery ──
            if self.cancel_event.is_set():
                logger.info("[Pipeline] Run cancelled before discovery stage.")
                report.success = False
                return report

            startups: List[Dict[str, Any]] = []
            if PipelineStage.DISCOVERY not in self.config.skip_stages:
                startups = await self._run_stage_discovery(report)
                if not report.stages[PipelineStage.DISCOVERY].success or not startups:
                    logger.error("[Pipeline] Discovery stage yielded no startups. Aborting pipeline.")
                    report.success = False
                    report.completed_at = datetime.now(timezone.utc)
                    report.total_duration_seconds = (
                        report.completed_at - report.started_at
                    ).total_seconds()
                    return report
            else:
                logger.info("[Pipeline] Skipping Discovery stage — using existing DB startups.")
                startups = self.storage.get_all_startups()
                report.total_startups_discovered = len(startups)

            # ── Dry Run Gate (stops after Discovery) ──
            if self.config.dry_run:
                logger.info("[Pipeline] Dry run enabled — stopping after discovery stage.")
                report.success = True
                report.completed_at = datetime.now(timezone.utc)
                report.total_duration_seconds = (
                    report.completed_at - report.started_at
                ).total_seconds()
                return report

            # ── Stage 2: Founder Extraction ──
            if self.cancel_event.is_set():
                logger.info("[Pipeline] Run cancelled before founder extraction.")
                report.success = False
                return report

            startup_ids: List[int] = []
            for s in startups:
                sid = s.get("startup_id") or s.get("id")
                if sid:
                    startup_ids.append(sid)

            if PipelineStage.FOUNDER not in self.config.skip_stages:
                extracted_ids = await self._run_stage_founders(startups, report)
                if extracted_ids:
                    startup_ids = list(set(startup_ids + extracted_ids))
            else:
                logger.info("[Pipeline] Skipping Founder stage.")

            # ── Stage 3: Fit Evaluation ──
            if self.cancel_event.is_set():
                logger.info("[Pipeline] Run cancelled before fit evaluation.")
                report.success = False
                return report

            location_scoped_ids = self._filter_by_target_location(startup_ids, report)

            if PipelineStage.FIT not in self.config.skip_stages:
                await self._run_stage_fit(location_scoped_ids, report)
            else:
                logger.info("[Pipeline] Skipping Fit evaluation stage.")

            # ── Qualification Gate (Deterministic Code Filter) ──
            all_qualified_ids = self.storage.get_qualified_startup_ids(
                min_score=self.config.min_fit_score
            )
            # Scope to startups evaluated in the current run if startup_ids is defined
            if location_scoped_ids:
                qualified_ids = [sid for sid in all_qualified_ids if sid in location_scoped_ids]
            else:
                qualified_ids = []

            report.total_qualified = len(qualified_ids)
            logger.info(
                f"[Pipeline Gate] {len(qualified_ids)} startups qualified in current run "
                f"(score >= {self.config.min_fit_score}) out of {len(startup_ids)} evaluated "
                f"(total qualified in DB: {len(all_qualified_ids)})."
            )

            # ── Stage 4: Message Drafting ──
            if self.cancel_event.is_set():
                logger.info("[Pipeline] Run cancelled before message drafting.")
                report.success = False
                return report

            if PipelineStage.MESSAGE not in self.config.skip_stages:
                if qualified_ids:
                    await self._run_stage_messages(qualified_ids, report)
                else:
                    logger.info("[Pipeline] No startups qualified for message drafting.")
                    report.stages[PipelineStage.MESSAGE] = StageResult(
                        stage=PipelineStage.MESSAGE,
                        success=True,
                        items_processed=0,
                        items_succeeded=0,
                        items_failed=0,
                        duration_seconds=0.0,
                    )
            else:
                logger.info("[Pipeline] Skipping Message drafting stage.")

            report.success = not self.cancel_event.is_set()

        except Exception as e:
            logger.error(f"[Pipeline] Fatal error during execution: {e}", exc_info=True)
            report.success = False

        finally:
            report.completed_at = datetime.now(timezone.utc)
            report.total_duration_seconds = (
                report.completed_at - report.started_at
            ).total_seconds()

            status = "completed" if report.success else ("cancelled" if self.cancel_event.is_set() else "failed")
            fit_stage = report.stages.get(PipelineStage.FIT)
            founder_stage = report.stages.get(PipelineStage.FOUNDER)
            message_stage = report.stages.get(PipelineStage.MESSAGE)
            stage_errors: List[str] = []
            for stage in report.stages.values():
                stage_errors.extend([f"{stage.stage.value}: {err}" for err in stage.errors])

            stats = {
                "discovered": report.total_startups_discovered,
                "founders": report.total_founders_extracted,
                "fit_evaluated": report.total_fit_evaluated,
                "location_matched": report.total_location_matched,
                "location_filtered": report.total_location_filtered,
                "location_unknown": report.total_location_unknown,
                "fit_failed": fit_stage.items_failed if fit_stage else 0,
                "founder_failed": founder_stage.items_failed if founder_stage else 0,
                "message_failed": message_stage.items_failed if message_stage else 0,
                "qualified": report.total_qualified,
                "drafts": report.total_drafts_generated,
            }
            error_summary = "Run cancelled by operator" if self.cancel_event.is_set() else None
            if stage_errors:
                detail = " | ".join(stage_errors[:12])
                error_summary = f"{error_summary} | {detail}" if error_summary else detail
                if len(error_summary) > 4000:
                    error_summary = error_summary[:3997] + "..."

            try:
                self.storage.record_pipeline_run_complete(
                    session_id=self.session_id,
                    status=status,
                    duration_seconds=report.total_duration_seconds,
                    stats=stats,
                    error_summary=error_summary,
                )
            except Exception as e:
                logger.warning(f"Failed to record pipeline run completion: {e}")

            # Clean up ephemeral session state
            try:
                self.memory.clear_session(self.session_id)
            except Exception as e:
                logger.debug(f"Failed to clear session: {e}")

            logger.info(
                f"🏁 [Pipeline Complete] status={status} success={report.success} | "
                f"duration={report.total_duration_seconds:.2f}s | "
                f"discovered={report.total_startups_discovered} | "
                f"location_matched={report.total_location_matched} | "
                f"qualified={report.total_qualified} | "
                f"drafts={report.total_drafts_generated}"
            )

        return report
