"""Demo data for the findings feature (no network, no LLM). Filled in by its milestone."""

from __future__ import annotations

from portolan.store.db import Database


def seed_findings(db: Database, paper_id: str) -> None:
    """Write the demo paper's findings data."""
