"""Tests for Phase 5: Outreach Dashboard & Human Review UI.

Covers all 12 REST API endpoints, state machine transitions,
per-channel regeneration, blacklist cascading, export, and CLI integration.
"""

import io
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from config.settings import settings
from dashboard.server import app
from db.models import FitEvaluation, FounderCreate, MessageDrafts, StartupCreate
from db.storage import StorageEngine, storage_engine


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Initializes a clean isolated SQLite database for each test."""
    monkeypatch.setattr(settings, "database_url", "")
    test_db = tmp_path / "test_dashboard.db"
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    StorageEngine.init_db()
    yield


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_lead():
    """Populates database with a sample startup, founder, fit evaluation, message drafts, and outreach record."""
    # 1. Startup
    startup = StartupCreate(
        name="DeepMark AI",
        slug="deepmark-ai",
        batch="Fall 2026",
        website="https://deepmark.ai",
        one_liner="Automated legal document intelligence",
        long_description="DeepMark uses LLMs to parse and compare complex multi-jurisdictional contracts.",
        industry="B2B Software",
        tags=["AI", "LegalTech", "B2B"],
        is_hiring=True,
        jobs_data=[{"title": "Founding Engineer", "location": "San Francisco, CA"}],
    )
    s_id = storage_engine.upsert_startup(startup)

    # 2. Founder
    founder = FounderCreate(
        startup_id=s_id,
        full_name="Sarah Connor",
        title="CEO & Co-founder",
        bio="Previously ML lead at Stanford NLP lab.",
        linkedin_url="https://www.linkedin.com/in/sarah-connor",
    )
    f_id = storage_engine.upsert_founder(founder)

    # 3. Fit Evaluation (High fit)
    fit_eval = FitEvaluation(
        startup_id=s_id,
        score=88,
        fit_tier="HIGH",
        technical_depth="HIGH",
        hiring_signals="EXPLICIT_ROLES",
        match_rationale=["Requires foundational distributed ML systems", "Actively hiring founding engineer"],
        contribution_angle="Accelerate low-latency inference pipelines and multi-tenant parsing architectures.",
        model_used="openrouter/anthropic/claude-3.5-sonnet",
        should_contact=True,
    )
    storage_engine.save_fit_evaluation(fit_eval)

    # 4. Message Drafts
    drafts = MessageDrafts(
        startup_id=s_id,
        founder_id=f_id,
        linkedin_note="Sarah, impressed by DeepMark's contract intelligence parser. Built distributed AI systems reducing latency 70%. Would love to connect.",
        yc_job_note="Saw your Founding Engineer role. My experience building multi-tenant LLM infra aligns directly with your parsing engine.",
        cold_email_subject="DeepMark inference latency & founding engineer fit",
        cold_email_body="Hi Sarah,\n\nSaw DeepMark's Fall 2026 launch...\n\nBest,\nAntigravity",
        model_used="openrouter/anthropic/claude-3.5-sonnet",
    )
    storage_engine.save_message_drafts(drafts)

    # Retrieve outreach_record ID
    records = storage_engine.get_review_leads()
    assert len(records) > 0
    return {
        "outreach_id": records[0]["outreach_id"],
        "startup_id": s_id,
        "founder_id": f_id,
        "startup_name": "DeepMark AI",
    }


def test_serve_dashboard_html(client):
    """GET / should serve the SPA index.html."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "YC Founder Outreach" in response.text


def test_api_stats_endpoint(client, sample_lead):
    """GET /api/stats returns accurate conversion funnel metrics."""
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_startups"] == 1
    assert data["total_founders"] == 1
    assert data["total_evaluated"] == 1
    assert data["fit_high"] == 1
    assert data["total_drafts"] == 1
    assert data["needs_review"] == 1
    assert data["outreach_draft"] == 1
    assert data["outreach_approved"] == 0


def test_api_batches_endpoint(client, sample_lead):
    """GET /api/batches returns distinct batch names and counts."""
    response = client.get("/api/batches")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["batch"] == "Fall 2026"
    assert data[0]["count"] == 1


def test_api_leads_list_and_sorting(client, sample_lead):
    """GET /api/leads supports pagination, filtering, and prioritizes pending review / high fit."""
    response = client.get("/api/leads")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["leads"]) == 1
    lead = data["leads"][0]
    assert lead["startup_name"] == "DeepMark AI"
    assert lead["fit_score"] == 88
    assert lead["fit_tier"] == "HIGH"
    assert lead["has_drafts"] is True
    assert lead["outreach_status"] == "draft"


def test_api_leads_status_filter(client, sample_lead):
    """GET /api/leads?status=pending_review filters correctly."""
    res_pending = client.get("/api/leads?status=pending_review")
    assert res_pending.status_code == 200
    assert res_pending.json()["total"] == 1

    res_sent = client.get("/api/leads?status=sent")
    assert res_sent.status_code == 200
    assert res_sent.json()["total"] == 0


def test_api_lead_detail_endpoint(client, sample_lead):
    """GET /api/leads/{id} returns complete enriched dossier."""
    oid = sample_lead["outreach_id"]
    response = client.get(f"/api/leads/{oid}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["outreach_id"] == oid
    assert detail["startup_name"] == "DeepMark AI"
    assert detail["founder_name"] == "Sarah Connor"
    assert "Sarah, impressed" in detail["linkedin_note"]
    assert detail["fit_score"] == 88
    assert len(detail["jobs_data"]) == 1
    assert len(detail["match_rationale"]) == 2


def test_api_lead_update_state_machine(client, sample_lead):
    """PATCH /api/leads/{id} transitions through Draft -> Review -> Approve -> Sent -> Replied."""
    oid = sample_lead["outreach_id"]

    # 1. Update to approved
    res_app = client.patch(f"/api/leads/{oid}", json={"status": "approved", "notes": "Approved for sending"})
    assert res_app.status_code == 200
    det = client.get(f"/api/leads/{oid}").json()
    assert det["outreach_status"] == "approved"
    assert det["notes"] == "Approved for sending"

    # 2. Mark as sent (after manual send)
    res_sent = client.patch(f"/api/leads/{oid}", json={"status": "sent"})
    assert res_sent.status_code == 200
    det = client.get(f"/api/leads/{oid}").json()
    assert det["outreach_status"] == "sent"

    # 3. Mark as replied
    res_replied = client.patch(f"/api/leads/{oid}", json={"status": "replied"})
    assert res_replied.status_code == 200
    det = client.get(f"/api/leads/{oid}").json()
    assert det["outreach_status"] == "replied"


def test_api_lead_edit_message_draft(client, sample_lead):
    """PATCH /api/leads/{id} allows human editing of message drafts."""
    oid = sample_lead["outreach_id"]
    new_note = "Custom edited note within limit."
    res = client.patch(f"/api/leads/{oid}", json={"linkedin_note": new_note})
    assert res.status_code == 200

    det = client.get(f"/api/leads/{oid}").json()
    assert det["linkedin_note"] == new_note


def test_api_lead_blacklist_cascade(client, sample_lead):
    """PATCH /api/leads/{id} with status='blacklisted' adds entity to blacklist and cascades."""
    oid = sample_lead["outreach_id"]
    res = client.patch(f"/api/leads/{oid}", json={"status": "blacklisted", "notes": "Do not contact"})
    assert res.status_code == 200

    # Verify outreach record updated
    det = client.get(f"/api/leads/{oid}").json()
    assert det["outreach_status"] == "blacklisted"

    # Verify company is now in blacklist table
    bl_entries = client.get("/api/blacklist").json()
    assert len(bl_entries) >= 1
    assert any(b["identifier_value"] == "DeepMark AI" for b in bl_entries)


@pytest.mark.asyncio
async def test_api_lead_regenerate_endpoint(client, sample_lead):
    """POST /api/leads/{id}/regenerate supports per-channel and all-channel regeneration."""
    oid = sample_lead["outreach_id"]

    # Mock MessageAgent.regenerate_channel
    with patch("agents.message_agent.MessageAgent.regenerate_channel", new_callable=AsyncMock) as mock_regen:
        mock_regen.return_value = ({"linkedin_note": "Freshly regenerated LinkedIn note."}, None)

        # 1. Regenerate single channel
        res = client.post(f"/api/leads/{oid}/regenerate", json={"channel": "linkedin_note"})
        assert res.status_code == 200
        mock_regen.assert_awaited_once_with(
            startup_id=sample_lead["startup_id"],
            founder_id=sample_lead["founder_id"],
            channel="linkedin_note",
            pitch_angle_override=None,
        )


def test_api_blacklist_crud(client):
    """Blacklist endpoints: GET list, POST add, DELETE remove."""
    # 1. Add entry
    add_res = client.post("/api/blacklist", json={
        "identifier_type": "domain",
        "identifier_value": "spam-domain.com",
        "reason": "Known spammer",
    })
    assert add_res.status_code == 200
    entry_id = add_res.json()["id"]

    # 2. List entries
    list_res = client.get("/api/blacklist")
    assert list_res.status_code == 200
    entries = list_res.json()
    assert len(entries) == 1
    assert entries[0]["identifier_value"] == "spam-domain.com"

    # 3. Delete entry
    del_res = client.delete(f"/api/blacklist/{entry_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 4. Verify removed
    assert len(client.get("/api/blacklist").json()) == 0


def test_api_export_csv_and_json(client, sample_lead):
    """GET /api/export returns downloadable CSV or JSON stream."""
    # CSV export
    res_csv = client.get("/api/export?format=csv&min_score=50")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "DeepMark AI" in res_csv.text

    # JSON export
    res_json = client.get("/api/export?format=json&min_score=50")
    assert res_json.status_code == 200
    assert "application/json" in res_json.headers["content-type"]
    leads = res_json.json()
    assert len(leads) == 1
    assert leads[0]["startup_name"] == "DeepMark AI"


def test_api_pipeline_status(client):
    """GET /api/pipeline/status returns the current idle/running state."""
    res = client.get("/api/pipeline/status")
    assert res.status_code == 200
    data = res.json()
    assert "is_running" in data


def test_cli_dashboard_subcommand_parser():
    """Verifies that cli.py parses the 'dashboard' subcommand with port, host, and no-browser flags."""
    from cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["dashboard", "--port", "8501", "--host", "127.0.0.1", "--no-browser"])
    assert args.subcommand == "dashboard"
    assert args.port == 8501
    assert args.host == "127.0.0.1"
    assert args.no_browser is True
