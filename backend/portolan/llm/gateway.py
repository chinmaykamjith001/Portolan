"""The LLM gateway: the only way any Portolan module calls an LLM (spec §11.1).

render template → cache key → cache hit? → rate-limit permit → provider → validate schema
(one repair retry) → cache → budget accounting. 429s back off with jitter and honor retry-after.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from portolan.config import PLACEHOLDER_MARK, Config
from portolan.llm.cache import LLMCache, cache_key, inputs_hash
from portolan.llm.prompts.base import PromptTemplate
from portolan.llm.providers import CallContext, FixtureStore, Provider, make_provider
from portolan.llm.ratelimit import BudgetStore, Clock, RateLimiter, RealClock, pacific_day
from portolan.llm.types import (
    LLMError,
    LLMResult,
    LLMValidationError,
    Message,
    Priority,
    ProviderQuotaExhausted,
    ProviderRateLimited,
    ProviderResponse,
    RateLimitExhausted,
    Role,
)
from portolan.store.db import Database

MAX_RATE_LIMIT_RETRIES = 6
BACKOFF_START_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

EventHandler = Callable[[str, dict[str, Any]], None]

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _parse(text: str, schema: type[BaseModel]) -> BaseModel:
    m = _FENCE.match(text)
    body = m.group(1) if m else text
    return schema.model_validate(json.loads(body))


def _estimate_tokens(messages: list[Message]) -> int:
    return sum(len(m.content) for m in messages) // 4 + 1


class Gateway:
    def __init__(
        self,
        config: Config,
        db: Database,
        *,
        provider: Provider | None = None,
        clock: Clock | None = None,
        recorder: FixtureStore | None = None,
        rng: random.Random | None = None,
        on_event: EventHandler | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or make_provider(config)
        self.cache = LLMCache(db)
        self.clock: Clock = clock or RealClock()
        self.limiter = RateLimiter(config.llm.limits, BudgetStore(db), self.clock)
        self.recorder = recorder
        self.rng = rng or random.Random()
        self.on_event: EventHandler = on_event or (lambda _type, _payload: None)
        self._exhausted: dict[str, str] = {}  # model -> Pacific day its provider quota ran out

    def model_for(self, role: Role) -> str:
        return self.config.llm.models.for_role(role)

    def budget_state(self) -> dict[str, object]:
        models = [self.model_for(r) for r in ("fast", "default", "strong")]
        state = self.limiter.state(models)
        state["provider"] = self.provider.name
        return state

    async def call(
        self,
        role: Role,
        template: PromptTemplate,
        inputs: dict[str, Any],
        schema: type[BaseModel] | None,
        priority: Priority,
        paper_id: str | None,
    ) -> LLMResult:
        model = self.model_for(role)
        if self.provider.uses_quota and PLACEHOLDER_MARK in model:
            raise LLMError(
                f"No model is configured for the '{role}' role. "
                "Run `portolan doctor` and copy its suggested [llm.models] into portolan.toml."
            )
        # Free tiers differ wildly per model (some Flash models allow 20 requests a day). When a
        # model's daily quota is gone, the role falls back to the fast model until the day resets.
        fallback = self.model_for("fast")
        can_fall_back = fallback != model and PLACEHOLDER_MARK not in fallback
        if self._quota_gone(model):
            hit = self._from_cache(model, role, template, inputs, schema)
            if hit is not None:
                return hit
            if not can_fall_back or self._quota_gone(fallback):
                raise ProviderQuotaExhausted(
                    f"Today's free quota for {model} is used up. It resets at midnight US Pacific."
                )
            return await self._call_model(fallback, role, template, inputs, schema, priority)
        try:
            return await self._call_model(model, role, template, inputs, schema, priority)
        except ProviderQuotaExhausted:
            self._exhausted[model] = pacific_day()
            if not can_fall_back or self._quota_gone(fallback):
                raise
            self.on_event("model_fallback", {"model": model, "fallback": fallback})
            try:
                return await self._call_model(fallback, role, template, inputs, schema, priority)
            except ProviderQuotaExhausted:
                self._exhausted[fallback] = pacific_day()
                raise

    def _quota_gone(self, model: str) -> bool:
        return self._exhausted.get(model) == pacific_day()

    def _from_cache(
        self,
        model: str,
        role: Role,
        template: PromptTemplate,
        inputs: dict[str, Any],
        schema: type[BaseModel] | None,
    ) -> LLMResult | None:
        in_hash = inputs_hash(inputs)
        cached = self.cache.get(cache_key(model, template.id, template.version, in_hash))
        if cached is None:
            return None
        try:
            parsed = _parse(cached.text, schema) if schema else None
        except (ValidationError, json.JSONDecodeError):
            return None  # stale entry from an older schema: regenerate
        if self.recorder is not None:  # a record run also captures cached answers
            self.recorder.write(
                CallContext(
                    template_id=template.id,
                    template_version=template.version,
                    inputs_hash=in_hash,
                    role=role,
                ),
                ProviderResponse(text=cached.text, usage=cached.usage, model=cached.model),
            )
        return LLMResult(
            parsed=parsed, raw_text=cached.text, usage=cached.usage, cache_hit=True,
            model=cached.model,
        )  # fmt: skip

    async def _call_model(
        self,
        model: str,
        role: Role,
        template: PromptTemplate,
        inputs: dict[str, Any],
        schema: type[BaseModel] | None,
        priority: Priority,
    ) -> LLMResult:
        hit = self._from_cache(model, role, template, inputs, schema)
        if hit is not None:
            return hit
        in_hash = inputs_hash(inputs)
        key = cache_key(model, template.id, template.version, in_hash)

        messages = template.render(inputs)
        ctx = CallContext(
            template_id=template.id,
            template_version=template.version,
            inputs_hash=in_hash,
            role=role,
            reasoning=template.reasoning,
        )
        parsed: BaseModel | None = None
        response: ProviderResponse | None = None
        for attempt in (0, 1):
            ctx = ctx.model_copy(update={"attempt": attempt})
            response = await self._complete(
                model, messages, template.temperature, schema, ctx, priority
            )
            if schema is None:
                break
            try:
                parsed = _parse(response.text, schema)
                break
            except (ValidationError, json.JSONDecodeError) as exc:
                if attempt == 1:
                    raise LLMValidationError(
                        f"Template '{template.id}' returned output that does not match "
                        f"{schema.__name__} after one retry."
                    ) from exc
                messages = [
                    *messages,
                    Message(role="assistant", content=response.text),
                    Message(
                        role="user",
                        content=(
                            "Your previous output was not valid. Error:\n"
                            f"{exc}\n"
                            "Return only JSON that matches the schema exactly."
                        ),
                    ),
                ]
        assert response is not None

        self.cache.put(
            key,
            model=response.model,
            template_id=template.id,
            template_version=template.version,
            text=response.text,
            usage=response.usage,
        )
        return LLMResult(
            parsed=parsed,
            raw_text=response.text,
            usage=response.usage,
            cache_hit=False,
            model=response.model,
        )

    def _backoff(self, retry: int, retry_after: float | None) -> float:
        if retry_after is not None and retry_after > 0:
            return min(retry_after, BACKOFF_MAX_SECONDS)
        base = min(BACKOFF_START_SECONDS * (2**retry), BACKOFF_MAX_SECONDS)
        return min(base + self.rng.uniform(0, base * 0.25), BACKOFF_MAX_SECONDS)

    async def _complete(
        self,
        model: str,
        messages: list[Message],
        temperature: float,
        schema: type[BaseModel] | None,
        ctx: CallContext,
        priority: Priority,
    ) -> ProviderResponse:
        for retry in range(MAX_RATE_LIMIT_RETRIES + 1):
            permit = None
            if self.provider.uses_quota:
                permit = await self.limiter.acquire(model, priority, _estimate_tokens(messages))
            try:
                response = await self.provider.complete(
                    model=model, messages=messages, temperature=temperature, schema=schema, ctx=ctx
                )
            except ProviderRateLimited as exc:
                wait = self._backoff(retry, exc.retry_after)
                self.limiter.cooldown(model, wait)
                self.on_event("rate_limited", {"model": model, "wait_seconds": round(wait, 1)})
                continue
            if permit is not None:
                self.limiter.record_usage(permit, response.usage)
            if self.recorder is not None:
                self.recorder.write(ctx, response)
            return response
        raise RateLimitExhausted(
            f"Still rate limited on {model} after {MAX_RATE_LIMIT_RETRIES} retries. "
            "Try again later."
        )


_gateway: Gateway | None = None


def init_gateway(config: Config, db: Database, **kwargs: Any) -> Gateway:
    global _gateway
    _gateway = Gateway(config, db, **kwargs)
    return _gateway


def get_gateway() -> Gateway:
    if _gateway is None:
        raise LLMError("The LLM gateway has not been initialised.")
    return _gateway


async def call(
    role: Role,
    template: PromptTemplate,
    inputs: dict[str, Any],
    schema: type[BaseModel] | None,
    priority: Priority,
    paper_id: str | None,
) -> LLMResult:
    return await get_gateway().call(role, template, inputs, schema, priority, paper_id)
