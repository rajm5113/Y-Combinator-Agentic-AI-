"""Tests for the Resilient LLM Fallback Engine using mocked OpenAI client responses."""

from unittest.mock import MagicMock
import pytest
from openai import RateLimitError
from config.llm_client import ModelTier, ResilientLLMClient, AllModelsExhaustedError


def make_mock_response(content: str, model: str):
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_choice.message.tool_calls = None
    mock_resp.choices = [mock_choice]
    mock_resp.model = model
    return mock_resp


def test_llm_client_first_model_success():
    """Verify that when first model succeeds, it returns immediately without fallback."""
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = make_mock_response(
        "Candidate matches perfectly", "nvidia/nemotron-3-ultra-550b-a55b:free"
    )

    client = ResilientLLMClient(client=mock_openai)
    result = client.call_with_fallback(
        tier=ModelTier.REASONING,
        messages=[{"role": "user", "content": "Assess fit"}],
    )

    assert result["content"] == "Candidate matches perfectly"
    assert result["model_used"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert result["attempts"] == 1


def test_llm_client_fallback_on_429_rate_limit(monkeypatch):
    """Verify automatic fallback to next model when primary model hits HTTP 429."""
    # Speed up backoff delay for fast test execution
    from config.settings import settings
    monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.01)
    monkeypatch.setattr(settings, "max_retries_per_model", 1)

    mock_openai = MagicMock()

    # Primary model triggers 429, secondary model succeeds
    rate_err = RateLimitError("Rate limit hit", response=MagicMock(status_code=429), body=None)
    success_resp = make_mock_response(
        "Fallback assessment succeeded", "nvidia/nemotron-3-super-120b-a12b:free"
    )

    mock_openai.chat.completions.create.side_effect = [rate_err, success_resp]

    client = ResilientLLMClient(client=mock_openai)
    result = client.call_with_fallback(
        tier=ModelTier.REASONING,
        messages=[{"role": "user", "content": "Assess fit"}],
    )

    assert result["content"] == "Fallback assessment succeeded"
    assert result["model_used"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert result["attempts"] == 2


def test_llm_client_all_models_exhausted(monkeypatch):
    """Verify AllModelsExhaustedError is raised when all models in the chain fail."""
    from config.settings import settings
    monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.01)
    monkeypatch.setattr(settings, "max_retries_per_model", 1)

    mock_openai = MagicMock()
    rate_err = RateLimitError("Rate limit", response=MagicMock(status_code=429), body=None)
    mock_openai.chat.completions.create.side_effect = rate_err

    client = ResilientLLMClient(client=mock_openai)
    with pytest.raises(AllModelsExhaustedError):
        client.call_with_fallback(
            tier=ModelTier.FAST,
            messages=[{"role": "user", "content": "Fast test"}],
        )
