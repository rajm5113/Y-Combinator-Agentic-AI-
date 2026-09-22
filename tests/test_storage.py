"""Tests for Relational Business Memory Engine and 'Never Contact Again' Blacklist."""

import pytest
from config.settings import settings
from db.models import (
    FitEvaluation,
    FounderCreate,
    MessageDrafts,
    StartupCreate,
)
from db.storage import StorageEngine


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Run each test with an isolated temporary SQLite database."""
    monkeypatch.setattr(settings, "database_url", "")
    test_db = tmp_path / "test_outreach.db"
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    StorageEngine.init_db()
    yield


def test_startup_and_founder_upsert():
    """Verify startup and founder insertion, foreign key linking, and deduplication."""
    startup_data = StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
        website="https://kailashlabs.com",
        one_liner="AI agents for enterprise data infrastructure",
        industry="Developer Tools",
        tags=["AI", "Agents", "B2B"],
        is_hiring=True,
    )
    startup_id = StorageEngine.upsert_startup(startup_data)
    assert startup_id > 0

    # Founder insertion
    founder_data = FounderCreate(
        startup_id=startup_id,
        full_name="Kailash Founder",
        title="CEO & Co-founder",
        bio="Previously distributed systems at Google",
        linkedin_url="https://linkedin.com/in/kailash-founder",
    )
    founder_id = StorageEngine.upsert_founder(founder_data)
    assert founder_id > 0

    # Upsert again with updated website (deduplication)
    startup_data.website = "https://new.kailashlabs.com"
    re_id = StorageEngine.upsert_startup(startup_data)
    assert re_id == startup_id


def test_fit_and_message_drafts():
    """Verify storing fit evaluation, generating drafts, and auto-creating outreach records."""
    startup_id = StorageEngine.upsert_startup(StartupCreate(
        name="Agentic Corp",
        slug="agentic-corp",
        batch="Summer 2026",
    ))
    founder_id = StorageEngine.upsert_founder(FounderCreate(
        startup_id=startup_id,
        full_name="Jane Doe",
        linkedin_url="https://linkedin.com/in/janedoe",
    ))

    # Save fit evaluation
    fit = FitEvaluation(
        startup_id=startup_id,
        score=88,
        fit_tier="HIGH",
        should_contact=True,
        match_rationale=["Strong agent architecture fit", "Looking for founding engineer"],
        contribution_angle="Accelerate agent graph orchestration",
        model_used="nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    eval_id = StorageEngine.save_fit_evaluation(fit)
    assert eval_id > 0

    # Save message drafts
    drafts = MessageDrafts(
        startup_id=startup_id,
        founder_id=founder_id,
        linkedin_note="Hi Jane, loved Agentic Corp's agent graph design. Would love to connect.",
        yc_job_note="Detailed job note here...",
        cold_email_subject="Quick note on agent architecture at Agentic Corp",
        cold_email_body="Full email body...",
        model_used="nvidia/nemotron-3-super-120b-a12b:free",
    )
    draft_id = StorageEngine.save_message_drafts(drafts)
    assert draft_id > 0

    # Check review leads
    leads = StorageEngine.get_review_leads()
    assert len(leads) == 1
    assert leads[0]["company_name"] == "Agentic Corp"
    assert leads[0]["fit_score"] == 88
    assert leads[0]["outreach_status"] == "draft"


def test_blacklist_memory_engine():
    """Verify 'Never Contact Again' blacklist engine prevents duplicate outreach."""
    startup_id = StorageEngine.upsert_startup(StartupCreate(
        name="Blacklisted Startup",
        slug="blacklisted-startup",
        batch="Fall 2026",
    ))
    founder_id = StorageEngine.upsert_founder(FounderCreate(
        startup_id=startup_id,
        full_name="Spammy Founder",
        linkedin_url="https://linkedin.com/in/spammy-founder",
    ))
    # Create draft outreach record
    StorageEngine.save_message_drafts(MessageDrafts(
        startup_id=startup_id,
        founder_id=founder_id,
        linkedin_note="Hi there",
    ))

    # Before blacklisting: is_blacklisted returns False
    assert not StorageEngine.is_blacklisted(
        founder_name="Spammy Founder",
        linkedin_url="https://linkedin.com/in/spammy-founder",
        company_name="Blacklisted Startup",
    )

    # Add to blacklist by LinkedIn URL
    StorageEngine.add_to_blacklist(
        identifier_type="linkedin_url",
        identifier_value="https://linkedin.com/in/spammy-founder",
        reason="Requested no further messages",
    )

    # After blacklisting: memory check returns True!
    assert StorageEngine.is_blacklisted(
        linkedin_url="https://linkedin.com/in/spammy-founder"
    )

    # Confirm active outreach record status was cascaded to 'blacklisted'
    leads = StorageEngine.get_review_leads(status_filter="blacklisted")
    assert len(leads) == 1
    assert leads[0]["founder_name"] == "Spammy Founder"

    # Dashboard stats verify counts
    stats = StorageEngine.get_dashboard_stats()
    assert stats["blacklisted"] == 1
