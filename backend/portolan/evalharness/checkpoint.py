"""Resumable results: one JSON line per finished record, keyed by (record id, engine version).

Append-only, so a crash or Ctrl-C loses at most the record in flight. A newer line for the same
key wins; changing the engine version (code or prompts) starts fresh without deleting old lines.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from portolan.models.finding import Finding


class RecordResult(BaseModel):
    record_id: str
    engine_version: str
    status: Literal["done", "failed"]
    findings: list[Finding] = Field(default_factory=list[Finding])
    error: str | None = None  # plain language, no traceback
    requests_used: int = 0
    note: str | None = None  # e.g. "requested commit abc, indexed HEAD def"
    finished_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[tuple[str, str], RecordResult]:
        results: dict[tuple[str, str], RecordResult] = {}
        if not self.path.exists():
            return results
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = RecordResult.model_validate_json(line)
            except ValueError:
                continue  # a torn last line from a crash: ignore it, the record just re-runs
            results[(r.record_id, r.engine_version)] = r
        return results

    def append(self, result: RecordResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(json.loads(result.model_dump_json()), ensure_ascii=False) + "\n")

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)
