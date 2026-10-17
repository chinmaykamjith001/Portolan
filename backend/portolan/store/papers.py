"""Persistence for parsed papers."""

from __future__ import annotations

from datetime import UTC, datetime

from portolan.models.paper import PaperDoc
from portolan.store.db import Database


def save_paper(db: Database, doc: PaperDoc) -> None:
    db.execute(
        "INSERT OR REPLACE INTO papers (paper_id, ref_json, doc_json, source_kind, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            doc.paper_id,
            doc.ref.model_dump_json(),
            doc.model_dump_json(),
            doc.ref.source_kind,
            datetime.now(UTC).isoformat(),
        ),
    )


def load_paper(db: Database, paper_id: str) -> PaperDoc | None:
    row = db.fetchone("SELECT doc_json FROM papers WHERE paper_id = ?", (paper_id,))
    return PaperDoc.model_validate_json(row["doc_json"]) if row else None
