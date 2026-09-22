"""Tests for centralized configuration and candidate profile knowledge base."""

import json
from pathlib import Path
from config.settings import settings


def test_settings_loaded():
    """Verify settings loaded with valid defaults and paths."""
    assert settings.project_root.exists()
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert len(settings.reasoning_fallback_chain) >= 2
    assert len(settings.extraction_fallback_chain) >= 2
    assert len(settings.fast_fallback_chain) >= 2
    assert "nvidia/nemotron-3-ultra-550b-a55b:free" in settings.reasoning_fallback_chain
    assert "google/gemma-4-31b-it:free" in settings.extraction_fallback_chain


def test_profile_json_structure():
    """Verify candidate profile exists, parses valid JSON, and has all explicit memory fields."""
    assert settings.profile_path.exists()

    with open(settings.profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    assert "candidate_name" in profile
    assert len(profile["candidate_name"]) > 0
    assert "headline" in profile
    assert "skills" in profile
    assert "languages" in profile["skills"]
    assert "target_roles" in profile
    assert isinstance(profile["target_roles"], list)
    assert len(profile["target_roles"]) > 0
    assert "pitch_angles" in profile
    assert "outreach_constraints" in profile
    assert profile["outreach_constraints"]["max_linkedin_note_chars"] <= 300


def test_linkedin_note_max_chars_setting():
    """Verify settings.linkedin_note_max_chars default and bounds (100 <= val <= 300)."""
    from pydantic import ValidationError
    from config.settings import Settings

    # Default is 300
    assert settings.linkedin_note_max_chars == 300

    # Configurable to 200
    s_200 = Settings(linkedin_note_max_chars=200)
    assert s_200.linkedin_note_max_chars == 200

    # > 300 raises ValidationError
    import pytest
    with pytest.raises(ValidationError):
        Settings(linkedin_note_max_chars=301)

    # < 100 raises ValidationError
    with pytest.raises(ValidationError):
        Settings(linkedin_note_max_chars=99)

