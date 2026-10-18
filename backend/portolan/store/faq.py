"""Persistence for FAQ items."""

from __future__ import annotations

from datetime import UTC, datetime

from portolan.models.finding import FAQItem
from portolan.store.db import Database


class FaqStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, paper_id: str, item: FAQItem) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO faq_items (paper_id, item_id, item_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (paper_id, item.id, item.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def all(self, paper_id: str) -> list[FAQItem]:
        rows = self.db.fetchall(
            "SELECT item_json FROM faq_items WHERE paper_id = ? ORDER BY created_at, item_id",
            (paper_id,),
        )
        return [FAQItem.model_validate_json(r["item_json"]) for r in rows]

    def has_any(self, paper_id: str) -> bool:
        return (
            self.db.fetchone("SELECT 1 AS x FROM faq_items WHERE paper_id = ? LIMIT 1", (paper_id,))
            is not None
        )

    def delete_paper(self, paper_id: str) -> None:
        self.db.execute("DELETE FROM faq_items WHERE paper_id = ?", (paper_id,))
