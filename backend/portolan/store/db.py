"""SQLite connection and migrations.

We use the standard-library `sqlite3` module directly (see docs/DECISIONS.md): the schema is small,
FTS5 needs raw SQL anyway, and this keeps the dependency list short.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from portolan.store.migrations import MIGRATIONS


class Database:
    """A single SQLite connection guarded by a lock (safe across FastAPI's threadpool)."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
            return row

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def schema_version(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return int(row[0] or 0)

    def migrate(self) -> int:
        """Apply pending migrations. Returns the resulting schema version."""
        current = self.schema_version()
        for version, _name, sql in MIGRATIONS:
            if version <= current:
                continue
            with self._lock:
                # executescript commits implicitly, so wrap the script in its own transaction.
                self._conn.executescript(
                    f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({version});\n"
                    "COMMIT;"
                )
            current = version
        return current


def open_database(path: Path | str) -> Database:
    db = Database(path)
    db.migrate()
    return db
