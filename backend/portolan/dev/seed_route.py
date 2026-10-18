"""Demo data for the route feature (no network, no LLM). Filled in by its milestone."""

from __future__ import annotations

from portolan.store.db import Database


def seed_route(db: Database, paper_id: str) -> None:
    """Write the demo paper's route data."""
