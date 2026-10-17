"""Persistence for repo candidates and the repo chosen for each paper."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from portolan.models.repo import RepoCandidate, RepoStatus
from portolan.store.db import Database


def save_candidates(
    db: Database, paper_id: str, candidates: list[RepoCandidate], status: RepoStatus
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO repo_candidates"
        " (paper_id, candidates_json, status_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (
            paper_id,
            json.dumps([c.model_dump(mode="json") for c in candidates]),
            status.model_dump_json(),
            datetime.now(UTC).isoformat(),
        ),
    )


def load_candidates(db: Database, paper_id: str) -> tuple[list[RepoCandidate], RepoStatus] | None:
    row = db.fetchone(
        "SELECT candidates_json, status_json FROM repo_candidates WHERE paper_id = ?", (paper_id,)
    )
    if row is None:
        return None
    raw: list[dict[str, object]] = json.loads(row["candidates_json"])
    return (
        [RepoCandidate.model_validate(c) for c in raw],
        RepoStatus.model_validate_json(row["status_json"]),
    )


def choose_repo(db: Database, paper_id: str, candidate: RepoCandidate) -> None:
    db.execute(
        "INSERT OR REPLACE INTO paper_repo (paper_id, candidate_json, chosen_at) VALUES (?, ?, ?)",
        (paper_id, candidate.model_dump_json(), datetime.now(UTC).isoformat()),
    )


def chosen_repo(db: Database, paper_id: str) -> RepoCandidate | None:
    row = db.fetchone("SELECT candidate_json FROM paper_repo WHERE paper_id = ?", (paper_id,))
    return RepoCandidate.model_validate_json(row["candidate_json"]) if row else None
