"""Verification engine (spec §10.9): paper claims against the linked code."""

from __future__ import annotations

from collections.abc import Callable

from portolan.llm.gateway import Gateway
from portolan.models.finding import Finding
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore
from portolan.store.db import Database


async def verify_paper(
    doc: PaperDoc,
    gateway: Gateway,
    chart: ChartStore,
    db: Database,
    on_finding: Callable[[Finding], None],
) -> None:
    """Extract claims, localize, run the check ladder, run the reverse pass. Saves each finding
    through FindingStore as it is decided, updates `issue_count_self` on mapped concepts, calls
    `on_finding` for each new finding, and records its progress in `verification_runs` so a
    stopped run resumes without repeating work. P2 priority; never raises for "no code"."""
    raise NotImplementedError("verification arrives with M8")
