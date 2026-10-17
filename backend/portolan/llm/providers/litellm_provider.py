"""Gemini and Ollama via LiteLLM. Only the gateway may use this module.

LiteLLM reads GEMINI_API_KEY from the environment; we never pass, log or store the key.
LiteLLM is imported lazily because it is slow to import and is never needed under the mock.
"""

from __future__ import annotations

import importlib
from typing import Any

from pydantic import BaseModel

from portolan.llm.providers.base import CallContext
from portolan.llm.types import (
    Message,
    ProviderError,
    ProviderRateLimited,
    ProviderResponse,
    Usage,
)


def _litellm() -> Any:
    module: Any = importlib.import_module("litellm")
    module.suppress_debug_info = True
    module.telemetry = False
    return module


def _retry_after(exc: Exception) -> float | None:
    response: Any = getattr(exc, "response", None)
    headers: Any = getattr(response, "headers", None)
    if headers is None:
        return None
    value: Any = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        try:
            resp: Any = await litellm.acompletion(**kwargs)
        except litellm.RateLimitError as exc:
            raise ProviderRateLimited(retry_after=_retry_after(exc)) from None
        except Exception as exc:
            # Exception text from providers can echo request details; keep only the type.
            raise ProviderError(
                f"The {self.name} provider failed ({type(exc).__name__})."
            ) from None

        text: str = resp.choices[0].message.content or ""
        usage: Any = getattr(resp, "usage", None)
        return ProviderResponse(
            text=text,
            usage=Usage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            ),
            model=str(getattr(resp, "model", model) or model),
        )
