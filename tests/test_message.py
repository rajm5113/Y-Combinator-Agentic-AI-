"""Tests for Multi-Channel Grounded Message Writer Agent (Spec 3.2).

Validates multi-channel drafting (LinkedIn <= 300 chars, YC job note, cold email),
enforcement of the Qualification Gate (skipping low-fit startups),
blacklist pre-check short-circuiting, and database persistence.
Zero live API calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.message_agent import MessageAgent, enforce_linkedin_limit
from db.connection import get_db_connection
from db.memory import memory_manager
from db.models import FitEvaluation, FounderCreate, StartupCreate
from db.storage import StorageEngine, storage_engine

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_message_response.json"


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean cache and DB for all message generation tests."""
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


@pytest.fixture
def mock_message_data() -> dict:
    """Loads the mock message generation response fixture."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ─── Helper Functions Tests ────────────────────────────────────────────────

def test_enforce_linkedin_limit_within_bounds():
    """Short note remains untouched."""
    note = "Hi Jane, loved Kailash Labs!"
    assert enforce_linkedin_limit(note, max_chars=300) == note


def test_enforce_linkedin_limit_truncates_overlong_note():
    """Overlong note is truncated cleanly to <= 300 chars."""
    overlong = (
        "Hi Jane, loved Kailash Labs and your focus on small video reasoning models. "
        "I built distributed AI agent systems that cut token overhead by 70% and low-latency APIs. "
        "We also worked on multiple high-throughput pipelines that served 15k+ daily requests without issues. "
        "I would really love to explore whether there is an opportunity to contribute to your core engineering team!"
    )
    assert len(overlong) > 300
    guarded = enforce_linkedin_limit(overlong, max_chars=300)
    assert len(guarded) <= 300
    assert not guarded.endswith(" ")


# ─── Message Agent Integration Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_message_generation_all_channels(mock_message_data: dict):
    """Verifies generation of LinkedIn note, YC job note, and cold email for high-fit startup."""
    # 1. Setup startup, founder, and HIGH fit evaluation
    sid = storage_engine.upsert_startup(StartupCreate(
        name="Kailash Labs",
        slug="kailash-labs",
        batch="Fall 2026",
        one_liner="Video reasoning models",
    ))
    fid = storage_engine.upsert_founder(FounderCreate(
        startup_id=sid,
        full_name="Jane Smith",
        title="CEO & Co-Founder",
        bio="Previously ML engineer at Google Brain",
        linkedin_url="https://www.linkedin.com/in/janesmith",
    ))
    storage_engine.save_fit_evaluation(FitEvaluation(
        startup_id=sid,
        score=88,
        fit_tier="HIGH",
        should_contact=True,
        match_rationale=["Strong technical match", "Hiring early team"],
        contribution_angle="Lead core distributed inference architecture",
    ))

    # 2. Mock LLM
    mock_llm = MagicMock()
    mock_llm.call_with_fallback.return_value = {
        "content": json.dumps(mock_message_data),
        "model_used": "meta-llama/llama-3.3-70b-instruct",
    }

    agent = MessageAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is True
    assert result.stats["drafts_generated"] == 1
    assert len(result.data) == 1

    draft = result.data[0]
    assert len(draft["linkedin_note"]) <= 300
    assert "Kailash" in draft["linkedin_note"]
    assert "Founding Engineer" in draft["yc_job_note"]
    assert "Video reasoning" in draft["cold_email_subject"]
    assert "Jane" in draft["cold_email_body"]

    # 3. Verify Business Memory persistence
    leads = storage_engine.get_review_leads()
    assert len(leads) == 1
    lead = leads[0]
    assert lead["company_name"] == "Kailash Labs"
    assert lead["founder_name"] == "Jane Smith"
    assert lead["fit_score"] == 88
    assert lead["linkedin_note"] == draft["linkedin_note"]
    assert lead["outreach_status"] == "draft"


@pytest.mark.asyncio
async def test_message_generation_skips_low_fit():
    """Verifies that startups with fit score < 50 are skipped at the qualification gate."""
    sid = storage_engine.upsert_startup(StartupCreate(
        name="BioNano Corp",
        slug="bionano-corp",
        batch="Fall 2026",
    ))
    storage_engine.upsert_founder(FounderCreate(
        startup_id=sid,
        full_name="Dr. Smith",
    ))
    # LOW fit score
    storage_engine.save_fit_evaluation(FitEvaluation(
        startup_id=sid,
        score=30,
        fit_tier="LOW",
        should_contact=False,
    ))

    mock_llm = MagicMock()
    agent = MessageAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is True
    assert result.stats["skipped_low_fit"] == 1
    assert result.stats["drafts_generated"] == 0
    assert mock_llm.call_with_fallback.call_count == 0  # Zero tokens consumed!


@pytest.mark.asyncio
async def test_message_generation_skips_blacklisted_founder():
    """Verifies that blacklisted founders are rejected before LLM generation."""
    sid = storage_engine.upsert_startup(StartupCreate(
        name="Target AI",
        slug="target-ai",
        batch="Fall 2026",
    ))
    storage_engine.upsert_founder(FounderCreate(
        startup_id=sid,
        full_name="Spammy Founder",
        linkedin_url="https://www.linkedin.com/in/spammy",
    ))
    storage_engine.save_fit_evaluation(FitEvaluation(
        startup_id=sid,
        score=85,
        fit_tier="HIGH",
        should_contact=True,
    ))

    # Add founder to blacklist
    storage_engine.add_to_blacklist(
        identifier_type="founder_name",
        identifier_value="Spammy Founder",
        reason="Do not contact",
    )

    mock_llm = MagicMock()
    agent = MessageAgent(llm_client=mock_llm)
    result = await agent.execute({"startup_id": sid})

    assert result.success is True
    assert result.stats["blacklisted_skips"] == 1
    assert result.stats["drafts_generated"] == 0
    assert mock_llm.call_with_fallback.call_count == 0


def test_message_draft_payload_schema_and_length():
    """Verify MessageDraftPayload validates and enforces the <= 300 char LinkedIn limit without truncation."""
    from agents.message_agent import MessageDraftPayload, validate_draft_payload

    # Valid payload
    valid_raw = json.dumps({
        "linkedin_note": "Hi Alice, saw your work on state machines.",
        "yc_job_note": "Here is why my background is a great fit.",
        "cold_email_subject": "State graphs for agent runtimes",
        "cold_email_body": "Paragraph 1\n\nParagraph 2\n\nParagraph 3",
    })
    draft = validate_draft_payload(valid_raw)
    assert isinstance(draft, MessageDraftPayload)
    assert len(draft.linkedin_note) <= 300

    # Overlong payload raises ValueError (no silent truncation)
    overlong_raw = json.dumps({
        "linkedin_note": "A" * 400,
        "yc_job_note": "Note",
        "cold_email_subject": "Subject",
        "cold_email_body": "Body",
    })
    with pytest.raises(ValueError, match="exceeds"):
        validate_draft_payload(overlong_raw)


# ─── Required Quality & Regeneration Tests (Sections 1-14) ─────────────────

def test_linkedin_note_300_chars_valid():
    """Test 1: A note of exactly 300 characters should pass."""
    from agents.message_agent import validate_draft_payload
    from config.settings import settings

    orig_limit = settings.linkedin_note_max_chars
    try:
        settings.linkedin_note_max_chars = 300
        exact_300 = "Saw Kailash Labs building video reasoning models. " + "A" * (300 - len("Saw Kailash Labs building video reasoning models. "))
        assert len(exact_300) == 300
        raw = json.dumps({
            "linkedin_note": exact_300,
            "yc_job_note": "Here is why my background is a great fit for your role.",
            "cold_email_subject": "Founding engineer fit & architectures",
            "cold_email_body": "Paragraph 1\n\nParagraph 2\n\nParagraph 3",
        })
        draft = validate_draft_payload(raw)
        assert len(draft.linkedin_note) == 300
    finally:
        settings.linkedin_note_max_chars = orig_limit


def test_linkedin_note_301_chars_fails():
    """Test 2: A 301-character note should fail validation."""
    from agents.message_agent import validate_draft_payload
    from config.settings import settings

    orig_limit = settings.linkedin_note_max_chars
    try:
        settings.linkedin_note_max_chars = 300
        note_301 = "A" * 301
        raw = json.dumps({
            "linkedin_note": note_301,
            "yc_job_note": "YC job note context",
            "cold_email_subject": "Email subject",
            "cold_email_body": "Cold email body",
        })
        with pytest.raises(ValueError, match="exceeds.*300"):
            validate_draft_payload(raw)
    finally:
        settings.linkedin_note_max_chars = orig_limit


def test_linkedin_note_200_chars_configurable():
    """Test 3: When linkedin_note_max_chars = 200, a 201-char note must fail, and 200-char passes."""
    from agents.message_agent import validate_draft_payload
    from config.settings import settings

    orig_limit = settings.linkedin_note_max_chars
    try:
        settings.linkedin_note_max_chars = 200
        # 200 characters passes
        note_200 = "A" * 200
        raw_valid = json.dumps({
            "linkedin_note": note_200,
            "yc_job_note": "YC job note content",
            "cold_email_subject": "Email subject",
            "cold_email_body": "Email body content",
        })
        draft = validate_draft_payload(raw_valid)
        assert len(draft.linkedin_note) == 200

        # 201 characters fails
        note_201 = "A" * 201
        raw_invalid = json.dumps({
            "linkedin_note": note_201,
            "yc_job_note": "YC job note content",
            "cold_email_subject": "Email subject",
            "cold_email_body": "Email body content",
        })
        with pytest.raises(ValueError, match="exceeds.*200"):
            validate_draft_payload(raw_invalid)
    finally:
        settings.linkedin_note_max_chars = orig_limit


def test_no_silent_truncation():
    """Test 4: Give the validator a >300-character note and verify the output is NOT silently shortened."""
    from agents.message_agent import validate_draft_payload
    from config.settings import settings

    orig_limit = settings.linkedin_note_max_chars
    try:
        settings.linkedin_note_max_chars = 300
        overlong_note = "A" * 350
        raw = json.dumps({
            "linkedin_note": overlong_note,
            "yc_job_note": "YC note",
            "cold_email_subject": "Email subject",
            "cold_email_body": "Email body",
        })
        # Must raise ValueError, never silently return a 300 char slice
        with pytest.raises(ValueError) as excinfo:
            validate_draft_payload(raw)
        assert "exceeds" in str(excinfo.value)
    finally:
        settings.linkedin_note_max_chars = orig_limit


@pytest.mark.asyncio
async def test_automatic_regeneration_on_length_failure():
    """Test 5: Mock Attempt 1 (301 chars) -> Attempt 2 (valid <=300), verify second accepted and persisted."""
    from agents.message_agent import MessageAgent
    from config.llm_client import ResilientLLMClient

    # Setup startup, founder, high fit
    sid = storage_engine.upsert_startup(StartupCreate(
        name="Regen AI",
        slug="regen-ai",
        batch="Fall 2026",
    ))
    fid = storage_engine.upsert_founder(FounderCreate(
        startup_id=sid,
        full_name="Alex Rivera",
        linkedin_url="https://www.linkedin.com/in/alexrivera",
    ))
    storage_engine.save_fit_evaluation(FitEvaluation(
        startup_id=sid,
        score=85,
        fit_tier="HIGH",
        should_contact=True,
    ))

    # Mock LLM chat completions
    # Call 1: 301-character note (fails length validation)
    # Call 2: valid <= 300 character note (succeeds)
    bad_resp = MagicMock()
    bad_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({
        "linkedin_note": "A" * 301,
        "yc_job_note": "Role requires distributed systems",
        "cold_email_subject": "Distributed architectures",
        "cold_email_body": "Paragraph 1\n\nParagraph 2\n\nParagraph 3",
    })))]

    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({
        "linkedin_note": "Alex, loved Regen's focus on video reasoning. Built multi-agent pipelines with async Postgres.",
        "yc_job_note": "Role requires distributed systems. I built async engines.",
        "cold_email_subject": "Distributed architectures & founding role",
        "cold_email_body": "Hi Alex,\n\nParagraph 1\n\nParagraph 2\n\nParagraph 3",
    })))]

    real_llm_client = ResilientLLMClient()
    mock_chat = MagicMock()
    mock_chat.completions.create.side_effect = [bad_resp, good_resp]
    real_llm_client.client = MagicMock(chat=mock_chat)

    agent = MessageAgent(llm_client=real_llm_client)
    drafts, err, skipped, blocked = await agent.draft_for_startup_and_founder(sid, founder_id=fid)

    assert drafts is not None
    assert err is None
    assert len(drafts.linkedin_note) <= 300
    assert "Regen" in drafts.linkedin_note
    assert mock_chat.completions.create.call_count == 2

    # Verify persisted in storage
    leads = storage_engine.get_review_leads()
    matched = [l for l in leads if l["company_name"] == "Regen AI"]
    assert len(matched) == 1
    assert matched[0]["linkedin_note"] == drafts.linkedin_note


def test_persistent_failure_triggers_fallback_model():
    """Test 6: Primary model fails retries -> fallback model succeeds."""
    from agents.message_agent import validate_draft_payload
    from config.llm_client import ModelTier, ResilientLLMClient

    client = ResilientLLMClient()
    mock_chat = MagicMock()

    # Primary model attempt 1 & 2 fail length check (>300 chars)
    bad_resp = MagicMock()
    bad_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({
        "linkedin_note": "X" * 350,
        "yc_job_note": "YC note",
        "cold_email_subject": "Subject",
        "cold_email_body": "Body",
    })))]

    # Fallback model attempt 1 succeeds
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({
        "linkedin_note": "Fallback model generated valid note.",
        "yc_job_note": "Fallback job note",
        "cold_email_subject": "Fallback subject",
        "cold_email_body": "Fallback body",
    })))]

    mock_chat.completions.create.side_effect = [bad_resp, bad_resp, good_resp]
    client.client = MagicMock(chat=mock_chat)

    res = client.call_with_fallback(
        tier=ModelTier.EXTRACTION,
        messages=[{"role": "user", "content": "Generate drafts"}],
        validator=validate_draft_payload,
    )

    assert res["validated_data"] is not None
    assert res["validated_data"].linkedin_note == "Fallback model generated valid note."
    assert mock_chat.completions.create.call_count == 3


def test_placeholder_rejection():
    """Test 7: Reject placeholders like [Founder Name], [Company], {founder_name}, {company}."""
    from agents.message_agent import validate_draft_payload

    # Test [Founder Name] and [Company]
    raw1 = json.dumps({
        "linkedin_note": "Hi [Founder Name], I saw [Company] is building agents.",
        "yc_job_note": "Role note",
        "cold_email_subject": "Subject",
        "cold_email_body": "Body",
    })
    with pytest.raises(ValueError, match="placeholder"):
        validate_draft_payload(raw1)

    # Test {founder_name}
    raw2 = json.dumps({
        "linkedin_note": "Hi {founder_name}, loved your approach.",
        "yc_job_note": "Role note",
        "cold_email_subject": "Subject",
        "cold_email_body": "Body",
    })
    with pytest.raises(ValueError, match="placeholder"):
        validate_draft_payload(raw2)

    # Test [Startup]
    raw3 = json.dumps({
        "linkedin_note": "Saw [Startup] is growing fast.",
        "yc_job_note": "Role note",
        "cold_email_subject": "Subject",
        "cold_email_body": "Body",
    })
    with pytest.raises(ValueError, match="placeholder"):
        validate_draft_payload(raw3)


def test_forbidden_buzzword_rejection():
    """Verify forbidden generic buzzwords (e.g. thrilled to connect, game-changing, synergy) are rejected."""
    from agents.message_agent import validate_draft_payload

    for buzz in ["thrilled to connect", "game-changing", "synergy", "pick your brain"]:
        raw = json.dumps({
            "linkedin_note": f"Loved your product and {buzz}. Let's chat.",
            "yc_job_note": "Role note",
            "cold_email_subject": "Subject",
            "cold_email_body": "Body",
        })
        with pytest.raises(ValueError, match="forbidden generic phrase"):
            validate_draft_payload(raw)


def test_channel_independence():
    """Test 8: Verify that channels are generated independently and not mechanically cloned."""
    from agents.message_agent import validate_draft_payload

    # If linkedin_note == yc_job_note, must fail validation
    cloned_raw = json.dumps({
        "linkedin_note": "Short identical note hook.",
        "yc_job_note": "Short identical note hook.",
        "cold_email_subject": "Subject line",
        "cold_email_body": "Separate body",
    })
    with pytest.raises(ValueError, match="Channels must be generated independently"):
        validate_draft_payload(cloned_raw)

    # If distinct, passes
    distinct_raw = json.dumps({
        "linkedin_note": "Short hook about vector pipelines.",
        "yc_job_note": "Detailed paragraph about your backend role and my experience building async Redis workers.",
        "cold_email_subject": "Vector pipelines and backend role",
        "cold_email_body": "Hi Jane,\n\nStudied your approach.\n\nMy experience.\n\nLet's connect.",
    })
    draft = validate_draft_payload(distinct_raw)
    assert draft.linkedin_note != draft.yc_job_note
    assert draft.linkedin_note != draft.cold_email_body



def test_call_with_fallback_validator_auto_repair():
    """Verify that call_with_fallback retries the same model when validation fails, then succeeds."""
    from agents.message_agent import validate_draft_payload
    from config.llm_client import ModelTier, ResilientLLMClient

    client = ResilientLLMClient()
    mock_chat = MagicMock()

    # Attempt 1: Truncated JSON
    bad_resp = MagicMock()
    bad_resp.choices = [MagicMock(message=MagicMock(content='{"linkedin_note": "Unterminated'))]

    # Attempt 2: Repaired valid JSON
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({
        "linkedin_note": "Repaired note",
        "yc_job_note": "Repaired job note",
        "cold_email_subject": "Subject",
        "cold_email_body": "Body",
    })))]

    mock_chat.completions.create.side_effect = [bad_resp, good_resp]
    client.client = MagicMock(chat=mock_chat)

    res = client.call_with_fallback(
        tier=ModelTier.EXTRACTION,
        messages=[{"role": "user", "content": "Generate drafts"}],
        validator=validate_draft_payload,
    )

    assert res["validated_data"] is not None
    assert res["validated_data"].linkedin_note == "Repaired note"
    assert mock_chat.completions.create.call_count == 2
    # Verify the second call received the repair feedback in messages
    call_args = mock_chat.completions.create.call_args_list[1][1]
    assert any("previous response was malformed" in m.get("content", "") for m in call_args["messages"])


def test_call_with_fallback_advances_to_fallback_model_on_persistent_invalid_json():
    """Verify that if model 1 persistently returns invalid JSON, it falls back to model 2."""
    from agents.message_agent import validate_draft_payload
    from config.llm_client import ModelTier, ResilientLLMClient

    client = ResilientLLMClient()
    mock_chat = MagicMock()

    # Model 1 fails twice (retries exhausted)
    bad_resp = MagicMock()
    bad_resp.choices = [MagicMock(message=MagicMock(content='malformed string'))]

    # Model 2 succeeds
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({
        "linkedin_note": "Model 2 note",
        "yc_job_note": "Model 2 job note",
        "cold_email_subject": "Model 2 subject",
        "cold_email_body": "Model 2 body",
    })))]

    mock_chat.completions.create.side_effect = [bad_resp, bad_resp, good_resp]
    client.client = MagicMock(chat=mock_chat)

    res = client.call_with_fallback(
        tier=ModelTier.EXTRACTION,
        messages=[{"role": "user", "content": "Generate drafts"}],
        validator=validate_draft_payload,
    )

    assert res["validated_data"] is not None
    assert res["validated_data"].linkedin_note == "Model 2 note"
