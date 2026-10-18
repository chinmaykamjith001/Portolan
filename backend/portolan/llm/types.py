"""Shared types for the LLM layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Priority = Literal["P0", "P1", "P2"]
Role = Literal["fast", "default", "strong"]
PRIORITY_ORDER: dict[Priority, int] = {"P0": 0, "P1": 1, "P2": 2}


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderResponse(BaseModel):
    text: str
    usage: Usage
    model: str


class LLMResult(BaseModel):
    parsed: Any = None  # instance of the requested schema, or None when no schema was given
    raw_text: str
    usage: Usage
    cache_hit: bool
    model: str


class LLMError(Exception):
    """Base class for gateway errors. Messages are plain language and never contain secrets."""


class ProviderRateLimited(LLMError):
    """The provider returned HTTP 429."""

    def __init__(self, message: str = "Rate limited by provider", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderTransient(ProviderRateLimited):
    """Overloaded / 5xx / timeout / connection drop: worth retrying with the same backoff."""


class ProviderQuotaExhausted(LLMError):
    """The provider's own daily quota (free tier) is used up. Parks the job until it resets.

    Named `...DailyBudgetExhausted`-like for the queue: it pauses rather than fails."""


class ProviderError(LLMError):
    """Any other provider failure."""


class LLMValidationError(LLMError):
    """The response did not match the schema, even after one retry."""


class MockFixtureMissing(LLMError):
    """The mock provider has no recorded response for this call."""

    def __init__(self, template_id: str, template_version: int, inputs_hash: str):
        super().__init__(
            f"No recorded response for template '{template_id}' v{template_version} "
            f"(inputs hash {inputs_hash}). Ask the owner to run `portolan record` for this step."
        )
        self.template_id = template_id
        self.template_version = template_version
        self.inputs_hash = inputs_hash


class RateLimitExhausted(LLMError):
    """Gave up after repeated 429s."""
