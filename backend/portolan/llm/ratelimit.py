"""Priority-aware rate limiter + persistent daily budget (spec §5.2, §11.2).

- RPM and TPM: sliding 60 s windows per model (in memory).
- RPD: persistent per-model counter in SQLite, keyed by the US Pacific calendar day
  (the free tier resets at midnight Pacific).
- P0 waits only for RPM/TPM. P1/P2 also leave `p0_reserve_rpm` per minute and
  `p0_reserve_rpd_fraction` of the day free for P0, and never start while a
  higher-priority request is waiting (preemption).
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from portolan.config import LimitsConfig
from portolan.llm.types import PRIORITY_ORDER, LLMError, Priority, Usage
from portolan.store.db import Database

PACIFIC = ZoneInfo("America/Los_Angeles")
WINDOW_SECONDS = 60.0
MAX_POLL_SECONDS = 1.0


def pacific_day() -> str:
    return datetime.now(PACIFIC).date().isoformat()


class Clock(Protocol):
    def now(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class DailyBudgetExhausted(LLMError):
    def __init__(self, model: str, used: int, cap: int, priority: Priority):
        who = "user requests" if priority == "P0" else "background work"
        super().__init__(
            f"Daily request budget for {model} is used up for {who} ({used}/{cap}). "
            "It resets at midnight US Pacific time."
        )
        self.model = model


class BudgetStore:
    def __init__(self, db: Database, day_fn: Callable[[], str] = pacific_day) -> None:
        self.db = db
        self.day_fn = day_fn

    def requests_today(self, model: str) -> int:
        row = self.db.fetchone(
            "SELECT requests FROM budget WHERE day = ? AND model = ?", (self.day_fn(), model)
        )
        return int(row["requests"]) if row else 0

    def add(self, model: str, *, requests: int = 0, usage: Usage | None = None) -> None:
        usage = usage or Usage()
        self.db.execute(
            "INSERT INTO budget (day, model, requests, input_tokens, output_tokens) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (day, model) DO UPDATE SET "
            "requests = requests + excluded.requests, "
            "input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens",
            (self.day_fn(), model, requests, usage.input_tokens, usage.output_tokens),
        )

    def today(self) -> list[dict[str, int | str]]:
        rows = self.db.fetchall(
            "SELECT model, requests, input_tokens, output_tokens FROM budget WHERE day = ? "
            "ORDER BY model",
            (self.day_fn(),),
        )
        return [dict(r) for r in rows]


@dataclass
class Permit:
    model: str
    priority: Priority
    started_at: float
    entry: list[float] = field(default_factory=lambda: [0.0, 0.0])  # [timestamp, tokens]


class RateLimiter:
    def __init__(
        self,
        limits: LimitsConfig,
        budget: BudgetStore,
        clock: Clock | None = None,
    ) -> None:
        self.limits = limits
        self.budget = budget
        self.clock: Clock = clock or RealClock()
        self._windows: dict[str, deque[list[float]]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._waiting: Counter[Priority] = Counter()

    # -- caps -------------------------------------------------------------------------------

    def rpm_cap(self, priority: Priority) -> int:
        if priority == "P0":
            return self.limits.rpm
        return max(1, self.limits.rpm - self.limits.p0_reserve_rpm)

    def rpd_cap(self, priority: Priority) -> int:
        if priority == "P0":
            return self.limits.rpd
        return int(self.limits.rpd * (1 - self.limits.p0_reserve_rpd_fraction))

    # -- state ------------------------------------------------------------------------------

    def _window(self, model: str) -> deque[list[float]]:
        window = self._windows.setdefault(model, deque())
        cutoff = self.clock.now() - WINDOW_SECONDS
        while window and window[0][0] <= cutoff:
            window.popleft()
        return window

    def _higher_priority_waiting(self, priority: Priority) -> bool:
        rank = PRIORITY_ORDER[priority]
        return any(n > 0 and PRIORITY_ORDER[p] < rank for p, n in self._waiting.items())

    def cooldown(self, model: str, seconds: float) -> None:
        until = self.clock.now() + seconds
        self._cooldown_until[model] = max(self._cooldown_until.get(model, 0.0), until)

    def cooldown_remaining(self, model: str) -> float:
        return max(0.0, self._cooldown_until.get(model, 0.0) - self.clock.now())

    def _wait_needed(self, model: str, priority: Priority, est_tokens: int) -> float:
        """Seconds to wait before this request may start (0 = go now)."""
        if self._higher_priority_waiting(priority):
            return MAX_POLL_SECONDS
        cooldown = self.cooldown_remaining(model)
        if cooldown > 0:
            return cooldown
        now = self.clock.now()
        window = self._window(model)
        if len(window) >= self.rpm_cap(priority):
            return window[0][0] + WINDOW_SECONDS - now
        used_tokens = sum(e[1] for e in window)
        if window and used_tokens + est_tokens > self.limits.tpm:
            return window[0][0] + WINDOW_SECONDS - now
        return 0.0

    # -- API --------------------------------------------------------------------------------

    async def acquire(self, model: str, priority: Priority, est_tokens: int = 0) -> Permit:
        used = self.budget.requests_today(model)
        cap = self.rpd_cap(priority)
        if used >= cap:
            raise DailyBudgetExhausted(model, used, cap, priority)

        self._waiting[priority] += 1
        try:
            while True:
                wait = self._wait_needed(model, priority, est_tokens)
                if wait <= 0:
                    break
                await self.clock.sleep(min(wait, MAX_POLL_SECONDS))
        finally:
            self._waiting[priority] -= 1

        now = self.clock.now()
        entry = [now, float(est_tokens)]
        self._window(model).append(entry)
        self.budget.add(model, requests=1)
        return Permit(model=model, priority=priority, started_at=now, entry=entry)

    def record_usage(self, permit: Permit, usage: Usage) -> None:
        """Replace the token estimate with real usage and add tokens to the daily budget."""
        permit.entry[1] = float(usage.input_tokens + usage.output_tokens)
        self.budget.add(permit.model, usage=usage)

    def state(self, models: list[str]) -> dict[str, object]:
        per_model: list[dict[str, object]] = []
        for model in dict.fromkeys(models):
            window = self._window(model)
            per_model.append(
                {
                    "model": model,
                    "requests_last_minute": len(window),
                    "tokens_last_minute": int(sum(e[1] for e in window)),
                    "requests_today": self.budget.requests_today(model),
                    "cooling_down_seconds": round(self.cooldown_remaining(model), 1),
                }
            )
        return {
            "day": self.budget.day_fn(),
            "limits": self.limits.model_dump(),
            "requests_today": sum(int(str(m["requests_today"])) for m in per_model),
            "models": per_model,
            "waiting": {p: n for p, n in self._waiting.items() if n > 0},
        }
