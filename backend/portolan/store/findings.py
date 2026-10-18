"""Persistence for findings and the state of a paper's verification run."""

from __future__ import annotations

from datetime import UTC, datetime

from portolan.models.finding import Finding
from portolan.store.db import Database


class FindingStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, paper_id: str, f: Finding) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO findings (paper_id, finding_id, finding_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (paper_id, f.id, f.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def all(self, paper_id: str) -> list[Finding]:
        rows = self.db.fetchall(
            "SELECT finding_json FROM findings WHERE paper_id = ? ORDER BY created_at, finding_id",
            (paper_id,),
        )
        return [Finding.model_validate_json(r["finding_json"]) for r in rows]

    def delete_paper(self, paper_id: str) -> None:
        self.db.execute("DELETE FROM findings WHERE paper_id = ?", (paper_id,))
        self.db.execute("DELETE FROM verification_runs WHERE paper_id = ?", (paper_id,))

    # -- run state (resumable, §5.2 rule 9) -----------------------------------------------

    def run_state(self, paper_id: str) -> str | None:
        row = self.db.fetchone(
            "SELECT state FROM verification_runs WHERE paper_id = ?", (paper_id,)
        )
        return row["state"] if row else None

    def set_run(
        self,
        paper_id: str,
        state: str,
        *,
        repo_commit: str | None = None,
        claims_json: str | None = None,
        progress_json: str | None = None,
    ) -> None:
        """Upsert; fields left as None keep their stored value."""
        self.db.execute(
            "INSERT INTO verification_runs"
            " (paper_id, state, repo_commit, claims_json, progress_json, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(paper_id) DO UPDATE SET state = excluded.state,"
            " repo_commit = COALESCE(excluded.repo_commit, repo_commit),"
            " claims_json = COALESCE(excluded.claims_json, claims_json),"
            " progress_json = COALESCE(excluded.progress_json, progress_json),"
            " updated_at = excluded.updated_at",
            (
                paper_id,
                state,
                repo_commit,
                claims_json,
                progress_json,
                datetime.now(UTC).isoformat(),
            ),
        )

    def run_data(self, paper_id: str) -> dict[str, str | None]:
        row = self.db.fetchone(
            "SELECT state, repo_commit, claims_json, progress_json FROM verification_runs"
            " WHERE paper_id = ?",
            (paper_id,),
        )
        return dict(row) if row else {}
