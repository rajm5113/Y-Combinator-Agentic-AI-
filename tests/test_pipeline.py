"""Unit and Integration Tests for Phase 4 Master Orchestrator and CLI.

All tests use mocked agent calls to ensure:
1. Zero token consumption and instant test execution
2. Deterministic assertions on pipeline DAG coordination
3. Complete coverage of validation, error containment, skip-stages, and CLI commands
"""

import asyncio
import csv
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from agents.base import AgentResult
from cli import build_parser, handle_blacklist, handle_export, handle_stats
from config.settings import settings
from db.models import FitEvaluation, FounderCreate, MessageDrafts, StartupCreate
from db.storage import StorageEngine
from pipeline import (
    MasterOrchestrator,
    PipelineConfig,
    PipelineReport,
    PipelineStage,
    StageResult,
)


@pytest.fixture(autouse=True)
def clean_test_environment():
    """Ensure clean cache and DB for all pipeline tests."""
    from db.connection import get_db_connection
    from db.memory import memory_manager

    memory_manager.clear_all_cache()
    StorageEngine.init_db()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM outreach_records")
        conn.execute("DELETE FROM message_drafts")
        conn.execute("DELETE FROM fit_evaluations")
        conn.execute("DELETE FROM founders")
        conn.execute("DELETE FROM startups")
        conn.execute("DELETE FROM blacklist")
    yield
    memory_manager.clear_all_cache()


# ─── PipelineConfig Validation Tests ────────────────────────────────────────

def test_pipeline_config_defaults():
    """Verify default configuration values."""
    config = PipelineConfig()
    assert config.min_fit_score == 50
    assert config.max_concurrency == 5
    assert not config.dry_run
    assert not config.force_refresh
    assert len(config.batches) > 0


def test_pipeline_config_validation_errors():
    """Verify boundary validation on score, limit, and concurrency."""
    with pytest.raises(ValidationError):
        PipelineConfig(min_fit_score=150)  # > 100

    with pytest.raises(ValidationError):
        PipelineConfig(min_fit_score=-5)  # < 0

    with pytest.raises(ValidationError):
        PipelineConfig(limit=0)  # < 1

    with pytest.raises(ValidationError):
        PipelineConfig(limit=1000)  # > 500

    with pytest.raises(ValidationError):
        PipelineConfig(max_concurrency=0)  # < 1

    with pytest.raises(ValidationError):
        PipelineConfig(max_concurrency=50)  # > 20


# ─── MasterOrchestrator DAG Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_full_run_success():
    """Test full 4-stage pipeline execution with mocked agents."""
    # Seed 2 startups
    s1_id = StorageEngine.upsert_startup(StartupCreate(
        name="Alpha Intelligence", slug="alpha-intel", batch="Fall 2026",
    ))
    s2_id = StorageEngine.upsert_startup(StartupCreate(
        name="Beta Robotics", slug="beta-robotics", batch="Fall 2026",
    ))

    # Add founders
    f1_id = StorageEngine.upsert_founder(FounderCreate(
        startup_id=s1_id, full_name="Alice Founder", linkedin_url="https://linkedin.com/in/alice",
    ))
    f2_id = StorageEngine.upsert_founder(FounderCreate(
        startup_id=s2_id, full_name="Bob Founder", linkedin_url="https://linkedin.com/in/bob",
    ))

    # Mock DiscoveryAgent
    mock_discovery_res = AgentResult(
        success=True,
        agent_name="discovery",
        data=[
            {"startup_id": s1_id, "slug": "alpha-intel", "name": "Alpha Intelligence"},
            {"startup_id": s2_id, "slug": "beta-robotics", "name": "Beta Robotics"},
        ],
    )

    # Mock FounderAgent
    mock_founder_res = AgentResult(
        success=True,
        agent_name="founder",
        data={"slug": "alpha-intel", "founders": 1},
    )

    # Mock FitAgent: Alpha qualifies (score=80), Beta does not (score=30)
    async def mock_fit_run(context):
        sid = context.get("startup_id")
        score = 80 if sid == s1_id else 30
        tier = "HIGH" if score >= 75 else "LOW"
        StorageEngine.save_fit_evaluation(FitEvaluation(
            startup_id=sid,
            score=score,
            fit_tier=tier,
            should_contact=(score >= 50),
            match_rationale=["Strong fit" if score >= 50 else "Weak fit"],
        ))
        return AgentResult(success=True, agent_name="fit", data={"score": score})

    # Mock MessageAgent
    async def mock_message_run(context):
        sid = context.get("startup_id")
        StorageEngine.save_message_drafts(MessageDrafts(
            startup_id=sid,
            founder_id=f1_id,
            linkedin_note="Hi Alice, saw your work at Alpha.",
        ))
        return AgentResult(success=True, agent_name="message", data={"startup_id": sid})

    with patch("pipeline.DiscoveryAgent") as mock_disc_cls, \
         patch("pipeline.FounderAgent") as mock_found_cls, \
         patch("pipeline.FitAgent") as mock_fit_cls, \
         patch("pipeline.MessageAgent") as mock_msg_cls:

        mock_disc_cls.return_value.run = AsyncMock(return_value=mock_discovery_res)
        mock_found_cls.return_value.run = AsyncMock(return_value=mock_founder_res)
        mock_fit_cls.return_value.run = AsyncMock(side_effect=mock_fit_run)
        mock_msg_cls.return_value.run = AsyncMock(side_effect=mock_message_run)

        config = PipelineConfig(batches=["Fall 2026"], min_fit_score=50)
        orchestrator = MasterOrchestrator(config=config)
        report = await orchestrator.run()

        assert isinstance(report, PipelineReport)
        assert report.success is True
        assert report.total_startups_discovered == 2
        assert report.total_fit_evaluated == 2
        assert report.total_qualified == 1  # Only Alpha passed score >= 50
        assert report.total_drafts_generated == 1
        assert PipelineStage.DISCOVERY in report.stages
        assert PipelineStage.FOUNDER in report.stages
        assert PipelineStage.FIT in report.stages
        assert PipelineStage.MESSAGE in report.stages


@pytest.mark.asyncio
async def test_pipeline_discovery_failure_aborts():
    """Verify that if discovery fails, the pipeline aborts without running downstream stages."""
    mock_disc_fail = AgentResult(
        success=False,
        agent_name="discovery",
        data=[],
        errors=["Network timeout contacting YC Algolia"],
    )

    with patch("pipeline.DiscoveryAgent") as mock_disc_cls, \
         patch("pipeline.FounderAgent") as mock_found_cls:

        mock_disc_cls.return_value.run = AsyncMock(return_value=mock_disc_fail)
        mock_found_cls.return_value.run = AsyncMock()

        orchestrator = MasterOrchestrator(PipelineConfig())
        report = await orchestrator.run()

        assert report.success is False
        assert report.total_startups_discovered == 0
        assert mock_found_cls.return_value.run.call_count == 0


@pytest.mark.asyncio
async def test_pipeline_partial_stage_failures():
    """Verify that individual item failure does not abort the entire pipeline (fail-soft)."""
    s1_id = StorageEngine.upsert_startup(StartupCreate(
        name="Startup One", slug="startup-one", batch="Fall 2026",
    ))
    s2_id = StorageEngine.upsert_startup(StartupCreate(
        name="Startup Two", slug="startup-two", batch="Fall 2026",
    ))

    # Discovery returns 2 startups
    mock_disc_res = AgentResult(
        success=True,
        agent_name="discovery",
        data=[
            {"startup_id": s1_id, "slug": "startup-one"},
            {"startup_id": s2_id, "slug": "startup-two"},
        ],
    )

    # Founder extraction succeeds for startup-one, fails for startup-two
    async def mock_founder_run(context):
        slugs = context.get("startup_slugs", [])
        if "startup-two" in slugs:
            return AgentResult(
                success=False, agent_name="founder", errors=["Inertia parse error"]
            )
        return AgentResult(success=True, agent_name="founder", data={"slug": "startup-one"})

    # Fit evaluation succeeds for startup-one
    async def mock_fit_run(context):
        sid = context.get("startup_id")
        StorageEngine.save_fit_evaluation(FitEvaluation(
            startup_id=sid, score=85, fit_tier="HIGH", should_contact=True,
        ))
        return AgentResult(success=True, agent_name="fit", data={"score": 85})

    with patch("pipeline.DiscoveryAgent") as mock_disc_cls, \
         patch("pipeline.FounderAgent") as mock_found_cls, \
         patch("pipeline.FitAgent") as mock_fit_cls, \
         patch("pipeline.MessageAgent") as mock_msg_cls:

        mock_disc_cls.return_value.run = AsyncMock(return_value=mock_disc_res)
        mock_found_cls.return_value.run = AsyncMock(side_effect=mock_founder_run)
        mock_fit_cls.return_value.run = AsyncMock(side_effect=mock_fit_run)
        mock_msg_cls.return_value.run = AsyncMock(return_value=AgentResult(
            success=True, agent_name="message",
        ))

        orchestrator = MasterOrchestrator(PipelineConfig())
        report = await orchestrator.run()

        # Pipeline overall succeeds because at least one progressed
        assert report.success is True
        founder_stage = report.stages[PipelineStage.FOUNDER]
        assert len(founder_stage.errors) > 0  # Captured failure for startup-two


@pytest.mark.asyncio
async def test_pipeline_dry_run():
    """Verify dry_run stops after discovery with zero LLM invocations."""
    mock_disc_res = AgentResult(
        success=True,
        agent_name="discovery",
        data=[{"startup_id": 1, "slug": "dry-startup", "name": "Dry Startup"}],
    )

    with patch("pipeline.DiscoveryAgent") as mock_disc_cls, \
         patch("pipeline.FounderAgent") as mock_found_cls, \
         patch("pipeline.FitAgent") as mock_fit_cls, \
         patch("pipeline.MessageAgent") as mock_msg_cls:

        mock_disc_cls.return_value.run = AsyncMock(return_value=mock_disc_res)
        mock_found_cls.return_value.run = AsyncMock()
        mock_fit_cls.return_value.run = AsyncMock()
        mock_msg_cls.return_value.run = AsyncMock()

        config = PipelineConfig(dry_run=True)
        orchestrator = MasterOrchestrator(config=config)
        report = await orchestrator.run()

        assert report.success is True
        assert report.total_startups_discovered == 1
        assert mock_found_cls.return_value.run.call_count == 0
        assert mock_fit_cls.return_value.run.call_count == 0
        assert mock_msg_cls.return_value.run.call_count == 0


@pytest.mark.asyncio
async def test_pipeline_skip_stages():
    """Verify skip_stages loads existing data from the database."""
    sid = StorageEngine.upsert_startup(StartupCreate(
        name="Existing Startup", slug="existing-startup", batch="Fall 2026",
    ))
    StorageEngine.upsert_founder(FounderCreate(
        startup_id=sid, full_name="Existing Founder", linkedin_url="https://linkedin.com/in/existing",
    ))

    with patch("pipeline.DiscoveryAgent") as mock_disc_cls, \
         patch("pipeline.FounderAgent") as mock_found_cls, \
         patch("pipeline.FitAgent") as mock_fit_cls:

        mock_disc_cls.return_value.run = AsyncMock()
        mock_found_cls.return_value.run = AsyncMock()
        mock_fit_cls.return_value.run = AsyncMock(return_value=AgentResult(
            success=True, agent_name="fit",
        ))

        config = PipelineConfig(skip_stages=[PipelineStage.DISCOVERY, PipelineStage.FOUNDER])
        orchestrator = MasterOrchestrator(config=config)
        report = await orchestrator.run()

        # Discovery and Founder were not called
        assert mock_disc_cls.return_value.run.call_count == 0
        assert mock_found_cls.return_value.run.call_count == 0
        assert report.total_startups_discovered == 1


# ─── CLI Subcommand Tests ───────────────────────────────────────────────────

def test_cli_parser_build():
    """Verify CLI parser commands and flags."""
    parser = build_parser()
    args = parser.parse_args(["run", "--batch", "Fall 2026", "--limit", "10", "--dry-run"])
    assert args.subcommand == "run"
    assert args.batch == ["Fall 2026"]
    assert args.limit == 10
    assert args.dry_run is True


def test_cli_stats_subcommand(capsys):
    """Verify CLI stats command displays summary table."""
    # Seed data
    sid = StorageEngine.upsert_startup(StartupCreate(
        name="StatCorp", slug="stat-corp", batch="Fall 2026",
    ))
    StorageEngine.upsert_founder(FounderCreate(
        startup_id=sid, full_name="Stat Founder",
    ))
    StorageEngine.save_fit_evaluation(FitEvaluation(
        startup_id=sid, score=90, fit_tier="HIGH", should_contact=True,
    ))

    parser = build_parser()
    args = parser.parse_args(["stats"])
    code = handle_stats(args)
    assert code == 0

    captured = capsys.readouterr()
    assert "YC FOUNDER OUTREACH -- DATABASE SUMMARY" in captured.out
    assert "Startups Discovered" in captured.out


def test_cli_blacklist_subcommands(capsys):
    """Verify CLI blacklist add, list, and remove."""
    parser = build_parser()

    # 1. Add
    args_add = parser.parse_args([
        "blacklist", "add",
        "--type", "company_name",
        "--value", "Spammy Inc",
        "--reason", "Unresponsive repeatedly",
    ])
    code_add = handle_blacklist(args_add)
    assert code_add == 0

    # 2. List
    args_list = parser.parse_args(["blacklist", "list"])
    code_list = handle_blacklist(args_list)
    assert code_list == 0
    captured = capsys.readouterr()
    assert "Spammy Inc" in captured.out

    # 3. Remove
    args_remove = parser.parse_args(["blacklist", "remove", "--value", "Spammy Inc"])
    code_remove = handle_blacklist(args_remove)
    assert code_remove == 0
    assert not StorageEngine.is_blacklisted(company_name="Spammy Inc")


def test_cli_export_csv_and_json(tmp_path):
    """Verify CSV and JSON export of qualified leads with drafts."""
    sid = StorageEngine.upsert_startup(StartupCreate(
        name="Lead AI", slug="lead-ai", batch="Fall 2026", website="https://lead.ai",
    ))
    fid = StorageEngine.upsert_founder(FounderCreate(
        startup_id=sid, full_name="Lead Founder", linkedin_url="https://linkedin.com/in/lead",
    ))
    StorageEngine.save_fit_evaluation(FitEvaluation(
        startup_id=sid, score=85, fit_tier="HIGH", should_contact=True,
        contribution_angle="AI infrastructure integration",
    ))
    StorageEngine.save_message_drafts(MessageDrafts(
        startup_id=sid, founder_id=fid, linkedin_note="Hi Lead Founder",
        cold_email_subject="Intro", cold_email_body="Full email",
    ))

    csv_out = tmp_path / "leads.csv"
    json_out = tmp_path / "leads.json"
    parser = build_parser()

    # CSV export
    args_csv = parser.parse_args([
        "export", "--min-score", "50", "--format", "csv", "--output", str(csv_out),
    ])
    code_csv = handle_export(args_csv)
    assert code_csv == 0
    assert csv_out.exists()

    with open(csv_out, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["startup_name"] == "Lead AI"
        assert rows[0]["founder_name"] == "Lead Founder"
        assert rows[0]["linkedin_note"] == "Hi Lead Founder"

    # JSON export
    args_json = parser.parse_args([
        "export", "--min-score", "50", "--format", "json", "--output", str(json_out),
    ])
    code_json = handle_export(args_json)
    assert code_json == 0
    assert json_out.exists()

    with open(json_out, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) == 1
        assert data[0]["startup_name"] == "Lead AI"
        assert data[0]["fit_score"] == 85
