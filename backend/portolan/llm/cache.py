"""Persistent LLM response cache (spec §11.3).

Key = sha256 of (model, template_id, template_version, normalized_inputs_hash).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from portolan.llm.types import Usage
from portolan.store.db import Database


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    if isinstance(value, str):
        # Line-ending and trailing-whitespace differences should not cause cache misses.
        return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").split("\n")).strip()
    if isinstance(value, dict):
        items: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {str(k): _normalize(v) for k, v in items.items()}
    if isinstance(value, list | tuple):
        seq: list[Any] | tuple[Any, ...] = value  # pyright: ignore[reportUnknownVariableType]
        return [_normalize(v) for v in seq]
    return value


def inputs_hash(inputs: dict[str, Any]) -> str:
    canonical = json.dumps(
        _normalize(inputs), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(model: str, template_id: str, template_version: int, in_hash: str) -> str:
    raw = json.dumps([model, template_id, template_version, in_hash], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CachedResponse(BaseModel):
    text: str
    usage: Usage
    model: str


class LLMCache:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> CachedResponse | None:
        row = self.db.fetchone(
            "SELECT model, response_json, usage_json FROM llm_cache WHERE key = ?", (key,)
        )
        if row is None:
            return None
        return CachedResponse(
            text=json.loads(row["response_json"])["text"],
            usage=Usage.model_validate_json(row["usage_json"]),
            model=row["model"],
        )

    def put(
        self,
        key: str,
        *,
        model: str,
        template_id: str,
        template_version: int,
        text: str,
        usage: Usage,
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO llm_cache "
            "(key, model, template_id, template_version, response_json, usage_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                model,
                template_id,
                template_version,
                json.dumps({"text": text}, ensure_ascii=False),
                usage.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )

    def invalidate_stale(self, template_id: str, current_version: int) -> int:
        """Delete entries of `template_id` from versions other than `current_version`."""
        before = self.count(template_id)
        self.db.execute(
            "DELETE FROM llm_cache WHERE template_id = ? AND template_version != ?",
            (template_id, current_version),
        )
        return before - self.count(template_id)

    def count(self, template_id: str | None = None) -> int:
        if template_id is None:
            row = self.db.fetchone("SELECT COUNT(*) FROM llm_cache")
        else:
            row = self.db.fetchone(
                "SELECT COUNT(*) FROM llm_cache WHERE template_id = ?", (template_id,)
            )
        return int(row[0]) if row else 0
