"""Demo data for the faq feature (no network, no LLM). Filled in by its milestone."""

from __future__ import annotations

from portolan.store.db import Database


def seed_faq(db: Database, paper_id: str) -> None:
    """Write the demo paper's faq data."""
