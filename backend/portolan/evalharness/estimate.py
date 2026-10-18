"""Cost estimate shown before any real request (spec §5.2 rule 8, §17)."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Spec §5.3 budgets verification at ~10-15 requests per paper (claim extraction, batched judge
# calls, the reverse pass). 15 is the pessimistic end; the report prints the measured number.
EST_REQUESTS_PER_RECORD = 15
CONFIRM_ABOVE = 30


@dataclass(frozen=True)
class Estimate:
    records: int
    requests: int
    daily_limit: int
    used_today: int

    @property
    def days(self) -> int:
        if self.requests == 0:
            return 0
        first = max(0, self.daily_limit - self.used_today)
        if self.requests <= first:
            return 1
        return 1 + math.ceil((self.requests - first) / max(1, self.daily_limit))


def estimate(records_to_run: int, *, daily_limit: int, used_today: int) -> Estimate:
    return Estimate(
        records=records_to_run,
        requests=records_to_run * EST_REQUESTS_PER_RECORD,
        daily_limit=daily_limit,
        used_today=used_today,
    )


def describe(e: Estimate) -> str:
    if e.records == 0:
        return "Nothing left to run: every sampled record already has a result."
    days = "one day" if e.days <= 1 else f"about {e.days} days (the run resumes where it stopped)"
    return (
        f"{e.records} record(s) to run, roughly {e.requests} real requests "
        f"(~{EST_REQUESTS_PER_RECORD} each; spec §5.3).\n"
        f"Free-tier limit ~{e.daily_limit} requests/day, {e.used_today} used today: "
        f"that is {days}."
    )


def needs_confirmation(e: Estimate) -> bool:
    return e.requests > CONFIRM_ABOVE
