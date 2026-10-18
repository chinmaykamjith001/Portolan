"""Gemini and Ollama via LiteLLM. Only the gateway may use this module.

LiteLLM reads GEMINI_API_KEY from the environment; we never pass, log or store the key.
LiteLLM is imported lazily because it is slow to import and is never needed under the mock.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from typing import Any

from pydantic import BaseModel

from portolan.llm.providers.base import CallContext
from portolan.llm.types import (
    Message,
    ProviderError,
    ProviderQuotaExhausted,
    ProviderRateLimited,
    ProviderResponse,
    ProviderTransient,
    Usage,
)

REQUEST_TIMEOUT_SECONDS = 120.0  # litellm's default is 6000 s: a stalled call would hold a worker
_RETRY_DELAY = re.compile(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s')
_PER_DAY = re.compile(r"per ?day|PerDay|RequestsPerDay", re.IGNORECASE)


def _litellm() -> Any:
    module: Any = importlib.import_module("litellm")
    module.suppress_debug_info = True
    module.telemetry = False
    # One "temperature is deprecated for Gemini 3" warning per call would flood the server log.
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    return module


def _retry_after(exc: Exception) -> float | None:
    """`retry-after` header, else Gemini's `retryDelay` ("37s") in the error body."""
    response: Any = getattr(exc, "response", None)
    headers: Any = getattr(response, "headers", None)
    value: Any = headers.get("retry-after") if headers is not None else None
    try:
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass
    found = _RETRY_DELAY.search(str(exc))
    return float(found.group(1)) if found else None


def _error(litellm: Any, exc: Exception, provider: str, model: str) -> Exception:
    """Map a LiteLLM exception to ours. Provider text can echo request details, so messages are
    our own words; only well-known facts (status class, model id from our config) go in them."""
    if isinstance(exc, litellm.RateLimitError):
        if _PER_DAY.search(str(exc)):
            return ProviderQuotaExhausted(
                f"{provider} says today's free quota for {model} is used up. "
                "It resets at midnight US Pacific time."
            )
        return ProviderRateLimited(retry_after=_retry_after(exc))
    transient = (
        litellm.ServiceUnavailableError, litellm.InternalServerError,
        litellm.Timeout, litellm.APIConnectionError,
    )  # fmt: skip
    if isinstance(exc, transient):
        return ProviderTransient(f"{provider} is busy or unreachable ({type(exc).__name__}).")
    if isinstance(exc, litellm.AuthenticationError) or (
        isinstance(exc, litellm.BadRequestError) and "api key" in str(exc).lower()
    ):
        return ProviderError(
            f"{provider} rejected the API key. Check GEMINI_API_KEY in .env, "
            "then restart the backend."
        )
    if isinstance(exc, litellm.NotFoundError):
        return ProviderError(
            f"{provider} doesn't offer the model {model!r}. "
            "Run `uv run portolan doctor` and update [llm.models] in portolan.toml."
        )
    if isinstance(exc, litellm.BadRequestError) and (detail := _provider_message(exc)):
        return ProviderError(f"{provider} refused the request: {detail}")
    return ProviderError(f"The {provider} provider failed ({type(exc).__name__}).")


_MESSAGE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.){1,400})"')


def _provider_message(exc: Exception) -> str | None:
    """Gemini's own one-line reason for a 400 ("Invalid value at 'generation_config...'"), with
    anything key-like masked. It names request fields, never prompt text."""
    found = _MESSAGE.search(str(exc))
    if not found:
        return None
    text = found.group(1).replace("\\n", " ").strip()
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        text = text.replace(key, "<key>")
    text = re.sub(r"(AIza|AQ\.)[A-Za-z0-9_\-]{10,}", "<key>", text)
    return text[:240]


class LiteLLMProvider:
    uses_quota = True

    def __init__(self, name: str, api_base: str | None = None) -> None:
        self.name = name
        self.api_base = api_base

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        schema: type[BaseModel] | None,
        ctx: CallContext,
    ) -> ProviderResponse:
        litellm = _litellm()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "num_retries": 0,  # the gateway owns retries and backoff
            "timeout": REQUEST_TIMEOUT_SECONDS,
        }
        if ctx.reasoning:
            kwargs["reasoning_effort"] = ctx.reasoning
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        try:
            resp: Any = await litellm.acompletion(**kwargs)
        except Exception as exc:
            raise _error(litellm, exc, self.name, model) from None

        choice: Any = resp.choices[0]
        text: str = choice.message.content or ""
        if not text.strip():
            # Blocked by safety, or all output tokens spent on reasoning: retrying the same prompt
            # as a "schema repair" can't help, so say what happened.
            reason = str(getattr(choice, "finish_reason", "") or "unknown")
            raise ProviderError(f"{self.name} returned an empty answer (finish reason: {reason}).")
        usage: Any = getattr(resp, "usage", None)
        return ProviderResponse(
            text=text,
            usage=Usage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            ),
            model=str(getattr(resp, "model", model) or model),
        )
