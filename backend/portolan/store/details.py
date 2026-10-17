"""Persistence for generated details (explanations, edge explanations)."""

from __future__ import annotations

from datetime import UTC, datetime

from portolan.models.detail import EdgeExplanation, Explanation
from portolan.store.db import Database


class DetailStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save_explanation(self, paper_id: str, e: Explanation) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO explanations"
            " (paper_id, concept_id, angle, explanation_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (paper_id, e.concept_id, e.angle, e.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def explanation(self, paper_id: str, concept_id: str, angle: str) -> Explanation | None:
        row = self.db.fetchone(
            "SELECT explanation_json FROM explanations"
            " WHERE paper_id = ? AND concept_id = ? AND angle = ?",
            (paper_id, concept_id, angle),
        )
        return Explanation.model_validate_json(row["explanation_json"]) if row else None

    def save_edge(self, paper_id: str, e: EdgeExplanation) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO edge_explanations"
            " (paper_id, link_id, explanation_json, created_at) VALUES (?, ?, ?, ?)",
            (paper_id, e.link_id, e.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def edge(self, paper_id: str, link_id: str) -> EdgeExplanation | None:
        row = self.db.fetchone(
            "SELECT explanation_json FROM edge_explanations WHERE paper_id = ? AND link_id = ?",
            (paper_id, link_id),
        )
        return EdgeExplanation.model_validate_json(row["explanation_json"]) if row else None
