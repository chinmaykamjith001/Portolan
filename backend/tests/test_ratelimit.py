from __future__ import annotations

import asyncio

import pytest

from portolan.config import LimitsConfig
from portolan.llm.ratelimit import BudgetStore, DailyBudgetExhausted, RateLimiter
from portolan.llm.types import Priority, Usage
from portolan.store.db import Database
from tests.conftest import FakeClock

MODEL = "gemini/test-flash"


def make(db: Database, clock: FakeClock, day: str = "2026-10-17", **limits: float) -> RateLimiter:
    cfg = LimitsConfig(**{"rpm": 4, "rpd": 100, "tpm": 1000, "p0_reserve_rpm": 2, **limits})  # type: ignore[arg-type]
    return RateLimiter(cfg, BudgetStore(db, day_fn=lambda: day), clock)


async def test_rpm_window_blocks_until_oldest_request_expires(
    db: Database, clock: FakeClock
) -> None:
    limiter = make(db, clock)
    start = clock.now()
    for _ in range(4):
        await limiter.acquire(MODEL, "P0")
    assert clock.now() == start  # no waiting yet
    await limiter.acquire(MODEL, "P0")
    assert clock.now() >= start + 60


async def test_background_work_leaves_rpm_reserve_for_p0(db: Database, clock: FakeClock) -> None:
    limiter = make(db, clock)
    start = clock.now()
    await limiter.acquire(MODEL, "P1")
    await limiter.acquire(MODEL, "P1")
    # P1 cap is rpm - reserve = 2, but P0 can still go immediately.
    await limiter.acquire(MODEL, "P0")
    assert clock.now() == start
    await limiter.acquire(MODEL, "P2")
    assert clock.now() >= start + 60


async def test_tpm_limit(db: Database, clock: FakeClock) -> None:
    limiter = make(db, clock, tpm=100)
    start = clock.now()
    await limiter.acquire(MODEL, "P0", est_tokens=80)
    await limiter.acquire(MODEL, "P0", est_tokens=50)
    assert clock.now() >= start + 60


async def test_priority_preemption(db: Database, clock: FakeClock) -> None:
    """When the window frees up, a waiting P0 goes before an earlier-queued P1."""
    limiter = make(db, clock, rpm=3, p0_reserve_rpm=0)
    for _ in range(3):
        await limiter.acquire(MODEL, "P0")
    order: list[Priority] = []

    async def worker(priority: Priority) -> None:
        await limiter.acquire(MODEL, priority)
        order.append(priority)

    p2 = asyncio.create_task(worker("P2"))
    p1 = asyncio.create_task(worker("P1"))
    await asyncio.sleep(0)
    p0 = asyncio.create_task(worker("P0"))
    await asyncio.gather(p0, p1, p2)
    assert order == ["P0", "P1", "P2"]


async def test_daily_budget_persists_and_reserves_for_p0(db: Database, clock: FakeClock) -> None:
    limiter = make(db, clock, rpm=1000, rpd=10, p0_reserve_rpd_fraction=0.2)
    for _ in range(8):
        await limiter.acquire(MODEL, "P1")
    with pytest.raises(DailyBudgetExhausted):
        await limiter.acquire(MODEL, "P1")
    await limiter.acquire(MODEL, "P0")
    await limiter.acquire(MODEL, "P0")
    with pytest.raises(DailyBudgetExhausted):
        await limiter.acquire(MODEL, "P0")

    # A new limiter on the same DB (e.g. after restart) sees the same count...
    assert make(db, clock).budget.requests_today(MODEL) == 10
    # ...and a new Pacific day starts from zero.
    assert make(db, clock, day="2026-10-18").budget.requests_today(MODEL) == 0


async def test_usage_accounting_and_state(db: Database, clock: FakeClock) -> None:
    limiter = make(db, clock)
    permit = await limiter.acquire(MODEL, "P0", est_tokens=500)
    limiter.record_usage(permit, Usage(input_tokens=30, output_tokens=12))
    state = limiter.state([MODEL, MODEL])
    assert state["requests_today"] == 1
    models = state["models"]
    assert isinstance(models, list) and len(models) == 1  # duplicate roles collapse
    assert models[0]["tokens_last_minute"] == 42
    row = limiter.budget.today()[0]
    assert (row["input_tokens"], row["output_tokens"]) == (30, 12)


async def test_cooldown_delays_requests(db: Database, clock: FakeClock) -> None:
    limiter = make(db, clock)
    start = clock.now()
    limiter.cooldown(MODEL, 20)
    await limiter.acquire(MODEL, "P0")
    assert clock.now() >= start + 20
