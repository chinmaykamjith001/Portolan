"""Ordered schema migrations. Append only; never edit a migration that has shipped."""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "llm cache and budget",
        """
        CREATE TABLE llm_cache (
            key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            usage_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX llm_cache_template ON llm_cache (template_id, template_version);

        -- One row per (Pacific-time day, model). RPD resets at midnight US Pacific.
        CREATE TABLE budget (
            day TEXT NOT NULL,
            model TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, model)
        );
        """,
    ),
]
