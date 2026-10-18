"""Deterministic sampling: the same data, split, limit and seed always pick the same records."""

from __future__ import annotations

import random
from typing import Literal

from portolan.evalharness.records import EvalRecord

SplitChoice = Literal["all", "real", "synthetic"]


def sample_records(
    records: list[EvalRecord], *, limit: int, seed: int, split: SplitChoice = "all"
) -> list[EvalRecord]:
    """Filter by split, order by id (so input order never matters), then draw `limit` with `seed`.
    The result keeps id order so reports and progress output are stable too."""
    pool = sorted(
        (r for r in records if split == "all" or r.split == split),
        key=lambda r: r.id,
    )
    if limit <= 0 or limit >= len(pool):
        return pool
    picked = random.Random(seed).sample(range(len(pool)), limit)
    return [pool[i] for i in sorted(picked)]
