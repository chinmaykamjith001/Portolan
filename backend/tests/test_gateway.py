from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from portolan.config import Config
from portolan.llm.cache import inputs_hash
from portolan.llm.gateway import Gateway
from portolan.llm.prompts.base import PromptTemplate
from portolan.llm.providers.base import CallContext, FixtureStore
from portolan.llm.providers.mock import MockProvider
from portolan.llm.types import (
    LLMError,
    LLMValidationError,
    Message,
    MockFixtureMissing,
    Priority,
    RateLimitExhausted,
)
from portolan.store.db import Database
from tests.conftest import FakeClock, ScriptedProvider, ok, rate_limited


class Gist(BaseModel):
    gist: str


def _render(inputs: dict[str, Any]) -> list[Message]:
    return [Message(role="user", content=f"Explain {inputs['concept']} as JSON.")]


TEMPLATE = PromptTemplate(id="test_gist", version=1, render=_render, schema=Gist)


def gateway(
    config: Config, db: Database, provider: Any, clock: FakeClock, **kw: Any
) -> tuple[Gateway, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    gw = Gateway(
        config,
        db,
        provider=provider,
        clock=clock,
        rng=random.Random(0),
        on_event=lambda t, p: events.append((t, p)),
        **kw,
    )
    return gw, events


async def test_second_identical_call_is_a_cache_hit(
    config: Config, db: Database, clock: FakeClock
) -> None:
    provider = ScriptedProvider([ok('{"gist": "weights that sum to 1"}')])
    gw, _ = gateway(config, db, provider, clock)
    first = await gw.call("default", TEMPLATE, {"concept": "softmax"}, Gist, "P0", None)
    second = await gw.call("default", TEMPLATE, {"concept": "softmax"}, Gist, "P0", None)
    assert not first.cache_hit and second.cache_hit
    assert second.parsed == Gist(gist="weights that sum to 1")
    assert len(provider.calls) == 1
    assert gw.limiter.budget.requests_today("gemini/test-flash") == 1


async def test_code_fences_are_accepted(config: Config, db: Database, clock: FakeClock) -> None:
    provider = ScriptedProvider([ok('```json\n{"gist": "g"}\n```')])
    gw, _ = gateway(config, db, provider, clock)
    result = await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert result.parsed == Gist(gist="g")


async def test_schema_failure_retries_once_with_the_error(
    config: Config, db: Database, clock: FakeClock
) -> None:
    provider = ScriptedProvider([ok('{"wrong": 1}'), ok('{"gist": "fixed"}')])
    gw, _ = gateway(config, db, provider, clock)
    result = await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert result.parsed == Gist(gist="fixed")
    assert len(provider.calls) == 2
    retry_messages = provider.calls[1][1]
    assert retry_messages[-1].role == "user"
    assert "not valid" in retry_messages[-1].content and "gist" in retry_messages[-1].content
    assert provider.calls[1][2].attempt == 1
    assert gw.limiter.budget.requests_today("gemini/test-flash") == 2


async def test_schema_failure_twice_raises_and_caches_nothing(
    config: Config, db: Database, clock: FakeClock
) -> None:
    provider = ScriptedProvider([ok("not json"), ok('{"still": "wrong"}')])
    gw, _ = gateway(config, db, provider, clock)
    with pytest.raises(LLMValidationError):
        await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert gw.cache.count() == 0


async def test_429_backs_off_exponentially_and_emits_events(
    config: Config, db: Database, clock: FakeClock
) -> None:
    provider = ScriptedProvider([rate_limited(), rate_limited(), ok('{"gist": "g"}')])
    gw, events = gateway(config, db, provider, clock)
    start = clock.now()
    result = await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert result.parsed == Gist(gist="g")
    waits = [p["wait_seconds"] for t, p in events if t == "rate_limited"]
    assert len(waits) == 2
    assert 2 <= waits[0] <= 2.5 and 4 <= waits[1] <= 5  # 2 s, then 4 s, plus ≤25% jitter
    assert clock.now() - start >= waits[0] + waits[1] - 0.2


async def test_429_honors_retry_after(config: Config, db: Database, clock: FakeClock) -> None:
    provider = ScriptedProvider([rate_limited(retry_after=17), ok('{"gist": "g"}')])
    gw, events = gateway(config, db, provider, clock)
    await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert events == [("rate_limited", {"model": "gemini/test-flash", "wait_seconds": 17})]


async def test_backoff_is_capped_and_eventually_gives_up(
    config: Config, db: Database, clock: FakeClock
) -> None:
    config.llm.limits.rpm = 1000
    provider = ScriptedProvider([rate_limited() for _ in range(7)])
    gw, events = gateway(config, db, provider, clock)
    with pytest.raises(RateLimitExhausted):
        await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert max(p["wait_seconds"] for _, p in events) <= 60


async def test_p0_preempts_queued_background_calls(
    config: Config, db: Database, clock: FakeClock
) -> None:
    config.llm.limits.rpm = 1
    config.llm.limits.p0_reserve_rpm = 0
    order: list[str] = []

    def step(label: str) -> Any:
        def respond() -> Any:
            order.append(label)
            return ok('{"gist": "g"}')

        return respond

    # Responses are handed out in call order, so label by the order we expect to be served.
    provider = ScriptedProvider([step("first"), step("second"), step("third")])
    gw, _ = gateway(config, db, provider, clock)
    await gw.call("default", TEMPLATE, {"concept": "warm-up"}, Gist, "P0", None)
    served: list[Priority] = []

    async def run(priority: Priority, concept: str) -> None:
        await gw.call("default", TEMPLATE, {"concept": concept}, Gist, priority, None)
        served.append(priority)

    background = asyncio.create_task(run("P2", "b"))
    await asyncio.sleep(0)
    user = asyncio.create_task(run("P0", "u"))
    await asyncio.gather(background, user)
    assert served == ["P0", "P2"]


async def test_placeholder_model_is_a_clear_error(db: Database, clock: FakeClock) -> None:
    gw, _ = gateway(Config(), db, ScriptedProvider([]), clock)
    with pytest.raises(LLMError, match="portolan doctor"):
        await gw.call("fast", TEMPLATE, {"concept": "x"}, Gist, "P0", None)


async def test_mock_missing_fixture_names_template_and_hash(
    tmp_path: Path, db: Database, clock: FakeClock
) -> None:
    gw, _ = gateway(Config(), db, MockProvider(tmp_path / "llm"), clock)
    with pytest.raises(MockFixtureMissing) as info:
        await gw.call("fast", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    message = str(info.value)
    assert "test_gist" in message and inputs_hash({"concept": "x"}) in message
    assert "portolan record" in message


async def test_record_then_replay_with_mock(
    config: Config, tmp_path: Path, db: Database, clock: FakeClock
) -> None:
    fixtures = tmp_path / "llm"
    live, _ = gateway(
        config,
        db,
        ScriptedProvider([ok('{"gist": "recorded"}')]),
        clock,
        recorder=FixtureStore(fixtures),
    )
    await live.call("default", TEMPLATE, {"concept": "softmax"}, Gist, "P0", None)
    ctx = CallContext(
        template_id="test_gist",
        template_version=1,
        inputs_hash=inputs_hash({"concept": "softmax"}),
        role="default",
    )
    fixture = FixtureStore(fixtures).path(ctx)
    assert fixture.is_file()
    assert "x-goog-api-key" not in fixture.read_text(encoding="utf-8").lower()

    fresh_db = Database(":memory:")
    fresh_db.migrate()
    replay, _ = gateway(Config(), fresh_db, MockProvider(fixtures), clock)
    result = await replay.call("default", TEMPLATE, {"concept": "softmax"}, Gist, "P0", None)
    assert result.parsed == Gist(gist="recorded")
    # The mock never touches the rate limiter or budget.
    assert replay.limiter.budget.today() == []
    fresh_db.close()


async def test_a_model_out_of_daily_quota_falls_back_to_the_fast_model(
    config: Config, db: Database, clock: FakeClock
) -> None:
    from portolan.llm.types import ProviderQuotaExhausted

    provider = ScriptedProvider(
        [
            ProviderQuotaExhausted("today's free quota for gemini/test-flash is used up"),
            ok('{"gist": "from the fast model"}'),
            ok('{"gist": "second, straight to the fast model"}'),
        ]
    )
    gw, events = gateway(config, db, provider, clock)
    first = await gw.call("default", TEMPLATE, {"concept": "softmax"}, Gist, "P0", None)
    assert first.parsed == Gist(gist="from the fast model")
    assert [m for m, _msgs, _ctx in provider.calls] == [
        "gemini/test-flash",
        "gemini/test-flash-lite",
    ]
    assert (
        "model_fallback",
        {"model": "gemini/test-flash", "fallback": "gemini/test-flash-lite"},
    ) in events

    # The exhausted model isn't asked again today: no wasted request, no wasted quota.
    second = await gw.call("strong", TEMPLATE, {"concept": "attention"}, Gist, "P0", None)
    assert second.parsed == Gist(gist="second, straight to the fast model")
    assert [m for m, _msgs, _ctx in provider.calls][-1] == "gemini/test-flash-lite"
    assert len(provider.calls) == 3


async def test_when_the_fast_model_is_out_too_the_job_pauses(
    config: Config, db: Database, clock: FakeClock
) -> None:
    from portolan.llm.types import ProviderQuotaExhausted

    gone = ProviderQuotaExhausted("used up")
    provider = ScriptedProvider([gone, gone])
    gw, _ = gateway(config, db, provider, clock)
    with pytest.raises(ProviderQuotaExhausted):
        await gw.call("default", TEMPLATE, {"concept": "softmax"}, Gist, "P0", None)
    with pytest.raises(ProviderQuotaExhausted):  # both known exhausted: no request at all
        await gw.call("default", TEMPLATE, {"concept": "x"}, Gist, "P0", None)
    assert len(provider.calls) == 2
