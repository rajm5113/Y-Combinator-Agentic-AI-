"""Resilient OpenRouter client wrapping OpenAI SDK with automated tiered fallback logic.

Handles rate limits (HTTP 429), timeouts, and service outages with
exponential backoff, jitter, and automated model rollover.
"""

import json
import logging
import random
import time
from enum import Enum
from typing import Any, Dict, List, Optional
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError
from config.settings import settings

logger = logging.getLogger("llm_client")


class ModelTier(str, Enum):
    REASONING = "reasoning"
    EXTRACTION = "extraction"
    FAST = "fast"


class AllModelsExhaustedError(Exception):
    """Raised when all models in the fallback chain have failed."""
    pass


class ResilientLLMClient:
    """Enterprise client for OpenRouter with automated model rollover."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Optional[OpenAI] = None,
    ):
        self.api_key = api_key or settings.openrouter_api_key or "mock-key"
        self.base_url = base_url or settings.openrouter_base_url
        
        # Allow injecting custom/mocked OpenAI client for deterministic unit testing
        if client is not None:
            self.client = client
        else:
            default_headers = {
                "HTTP-Referer": settings.site_url,
                "X-Title": settings.app_name,
            }
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=default_headers,
            )

    def get_fallback_chain(self, tier: ModelTier) -> List[str]:
        """Returns the configured model chain for a given capability tier."""
        if tier == ModelTier.REASONING:
            return list(settings.reasoning_fallback_chain)
        elif tier == ModelTier.EXTRACTION:
            return list(settings.extraction_fallback_chain)
        elif tier == ModelTier.FAST:
            return list(settings.fast_fallback_chain)
        else:
            return list(settings.fast_fallback_chain)

    def call_with_fallback(
        self,
        tier: ModelTier = ModelTier.FAST,
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        validator: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Executes a chat completion across the model fallback chain until success.

        Optionally validates response content using the provided validator callable.
        If validation fails, retries the same model with repair feedback before
        falling back to the next model in the chain.

        Returns:
            Dict containing:
                - "content": str (response text, if any)
                - "validated_data": Any (result of validator if provided)
                - "tool_calls": list (if tools were called)
                - "raw_response": ChatCompletion object
                - "model_used": str (actual model that succeeded)
                - "attempts": int (total call attempts)
                - "tier": str (tier requested)
        """
        if messages is None:
            messages = []

        chain = self.get_fallback_chain(tier)
        attempt_count = 0
        last_error = None

        for model in chain:
            current_messages = list(messages)
            for retry in range(settings.max_retries_per_model):
                attempt_count += 1
                try:
                    kwargs: Dict[str, Any] = {
                        "model": model,
                        "messages": current_messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                    if tools:
                        kwargs["tools"] = tools
                    if tool_choice:
                        kwargs["tool_choice"] = tool_choice
                    if response_format:
                        kwargs["response_format"] = response_format

                    response = self.client.chat.completions.create(**kwargs)
                    choice = response.choices[0]
                    message = choice.message
                    content = message.content or ""

                    validated_data = None
                    if validator:
                        try:
                            validated_data = validator(content)
                        except Exception as val_err:
                            last_error = val_err
                            is_length_error = "exceeds" in str(val_err).lower() and "character" in str(val_err).lower()
                            reason = "length_validation" if is_length_error else "schema_validation"

                            telemetry = {
                                "channel": "linkedin" if (is_length_error or "linkedin" in str(val_err).lower()) else "outreach",
                                "attempt": retry + 1,
                                "total_attempts": attempt_count,
                                "reason": reason,
                                "max_chars": getattr(settings, "linkedin_note_max_chars", 300),
                                "model_used": model,
                                "validation_error": str(val_err),
                            }
                            logger.warning(f"Validation retry telemetry: {json.dumps(telemetry)}")

                            if retry + 1 < settings.max_retries_per_model:
                                max_c = getattr(settings, "linkedin_note_max_chars", 300)
                                if is_length_error:
                                    repair_prompt = (
                                        "Your previous output failed validation.\n\n"
                                        f"Problem:\n{val_err}\n\n"
                                        "Regenerate the entire response.\n"
                                        "Return ONLY valid JSON.\n"
                                        "Keep all required fields.\n"
                                        "Do not truncate the previous response.\n"
                                        f"Rewrite the LinkedIn note so it satisfies the limit of {max_c} characters naturally."
                                    )
                                else:
                                    repair_prompt = (
                                        "Your previous output failed validation.\n\n"
                                        f"Problem:\nYour previous response was malformed or failed validation: {val_err}.\n\n"
                                        "Regenerate the entire response.\n"
                                        "Return ONLY valid JSON.\n"
                                        "Keep all required fields.\n"
                                        "It must be 100% complete, valid JSON strictly adhering to the schema."
                                    )
                                current_messages = list(messages) + [
                                    {"role": "assistant", "content": content},
                                    {"role": "user", "content": repair_prompt},
                                ]
                                continue
                            else:
                                logger.warning(
                                    f"Retries exhausted on model {model} due to validation errors. Falling back to next model..."
                                )
                                break

                    return {
                        "content": content,
                        "validated_data": validated_data,
                        "tool_calls": getattr(message, "tool_calls", None),
                        "raw_response": response,
                        "model_used": model,
                        "attempts": attempt_count,
                        "tier": tier.value,
                        "retry_count": retry,
                    }

                except (RateLimitError, APIStatusError, APIConnectionError) as e:
                    last_error = e
                    status_code = getattr(e, "status_code", None)
                    logger.warning(
                        f"Model {model} failed (attempt {retry + 1}/{settings.max_retries_per_model}) "
                        f"with status {status_code}: {e}. Retrying with backoff..."
                    )
                    # Exponential backoff with jitter
                    base_delay = settings.retry_base_delay_seconds * (2 ** retry)
                    jitter = random.uniform(0, settings.jitter_factor * base_delay)
                    delay = min(base_delay + jitter, settings.retry_max_delay_seconds)
                    time.sleep(delay)

                except Exception as e:
                    last_error = e
                    logger.warning(f"Unexpected error calling model {model}: {e}")
                    break  # Fall back to next model in chain

        raise AllModelsExhaustedError(
            f"All models in fallback chain for tier '{tier.value}' exhausted. Last error: {last_error}"
        )


llm_client = ResilientLLMClient()
