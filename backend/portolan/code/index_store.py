"""Persistence for code indexes (spec §10.4 steps 3-5): files, symbols, imports, calls and FTS rows.

One (repo_id, commit_sha) pair is one index. `save` replaces it wholesale in one transaction, so
a re-index never leaves a mix of old and new rows behind.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

from portolan.models.code import CallRef, CodeIndex, FileIndex, ImportRef, Symbol
from portolan.store.db import Database

BODY_CHARS = 4_000  # cap on the source text indexed per symbol

_WORD_RUN = re.compile(r"[A-Za-z0-9]+")
_CAMEL_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")

_SYMBOL_COLUMNS = (
    "symbol_id, kind, name, qualname, path, start_line, end_line,"
    " docstring, signature, value, parent"
)
_INDEX_TABLES = (
    "code_indexes",
    "code_files",
    "code_symbols",
    "code_imports",
    "code_calls",
    "code_embeddings",  # vectors belong to the symbols of one index, so they go with it
)


def identifier_words(text: str) -> list[str]:
    """Split snake_case, CamelCase, kebab-case and dotted names into lowercase words.

    "MultiHeadAttention" → ["multi", "head", "attention"]; "d_model" → ["d", "model"];
    "--label-smoothing" → ["label", "smoothing"]; "HTTPServer2" → ["http", "server", "2"].
    """
    words: list[str] = []
    for run in _WORD_RUN.findall(text):
        words.extend(part.lower() for part in _CAMEL_PART.findall(run))
    return words


def repo_key(repo_id: str, commit_sha: str) -> str:
    """The key that scopes FTS rows to one index."""
    return f"{repo_id}@{commit_sha}"


def split_lines(text: str) -> list[str]:
    """Lines as Python's tokenizer counts them (\\n, \\r\\n, \\r), without a phantom last line."""
    if not text:
        return []
    parts = _LINE_BREAK.split(text)
    if parts[-1] == "":
        parts.pop()
    return parts


def symbol_from_row(row: sqlite3.Row) -> Symbol:
    return Symbol(
        id=row["symbol_id"],
        kind=row["kind"],
        name=row["name"],
        qualname=row["qualname"],
        path=row["path"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        docstring=row["docstring"],
        signature=row["signature"],
        value=row["value"],
        parent=row["parent"],
    )


def symbol_body(lines: list[str], symbol: Symbol) -> str:
    """A symbol's source lines, capped at BODY_CHARS."""
    start = max(symbol.start_line, 1) - 1
    end = max(symbol.end_line, symbol.start_line)
    return "\n".join(lines[start:end])[:BODY_CHARS]


class IndexStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- writing -------------------------------------------------------------------------------

    def save(self, index: CodeIndex, files: list[FileIndex], texts: dict[str, str]) -> None:
        """Replace everything stored for (index.repo_id, index.commit_sha).

        `texts` maps a path to its source; a notebook falls back to `FileIndex.virtual_text`.
        """
        repo_id, sha = index.repo_id, index.commit_sha
        key = repo_key(repo_id, sha)
        file_rows: list[tuple[object, ...]] = []
        symbol_rows: list[tuple[object, ...]] = []
        fts_rows: list[tuple[object, ...]] = []
        import_rows: list[tuple[object, ...]] = []
        call_rows: list[tuple[object, ...]] = []
        for f in files:
            text = texts.get(f.path)
            if text is None:
                text = f.virtual_text or ""
            lines = split_lines(text)
            file_rows.append((repo_id, sha, f.path, f.language, f.sha256, f.n_lines, text))
            for s in f.symbols:
                symbol_rows.append(
                    (
                        repo_id,
                        sha,
                        s.id,
                        s.kind,
                        s.name,
                        s.qualname,
                        s.path,
                        s.start_line,
                        s.end_line,
                        s.docstring,
                        s.signature,
                        s.value,
                        s.parent,
                    )
                )
                fts_rows.append(
                    (
                        s.id,
                        key,
                        s.name,
                        " ".join(identifier_words(s.name)),
                        s.docstring or "",
                        symbol_body(lines, s),
                        s.path,
                    )
                )
            import_rows.extend(
                (repo_id, sha, i.path, i.line, i.module, json.dumps(i.names), i.alias)
                for i in f.imports
            )
            call_rows.extend((repo_id, sha, c.path, c.line, c.callee, c.in_symbol) for c in f.calls)

        with self.db.transaction() as conn:
            self._delete(conn, repo_id, sha)
            conn.execute(
                "INSERT INTO code_indexes (repo_id, commit_sha, root, index_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (repo_id, sha, index.root, index.model_dump_json(), datetime.now(UTC).isoformat()),
            )
            conn.executemany(
                "INSERT INTO code_files"
                " (repo_id, commit_sha, path, language, sha256, n_lines, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                file_rows,
            )
            conn.executemany(
                f"INSERT INTO code_symbols (repo_id, commit_sha, {_SYMBOL_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                symbol_rows,
            )
            conn.executemany(
                "INSERT INTO code_fts"
                " (symbol_id, repo_key, name, name_parts, docstring, body, path)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                fts_rows,
            )
            conn.executemany(
                "INSERT INTO code_imports"
                " (repo_id, commit_sha, path, line, module, names_json, alias)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                import_rows,
            )
            conn.executemany(
                "INSERT INTO code_calls (repo_id, commit_sha, path, line, callee, in_symbol)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                call_rows,
            )

    def delete(self, repo_id: str, commit_sha: str) -> None:
        """Remove the index, its FTS rows and its embeddings."""
        with self.db.transaction() as conn:
            self._delete(conn, repo_id, commit_sha)

    @staticmethod
    def _delete(conn: sqlite3.Connection, repo_id: str, sha: str) -> None:
        for table in _INDEX_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE repo_id = ? AND commit_sha = ?", (repo_id, sha)
            )
        conn.execute("DELETE FROM code_fts WHERE repo_key = ?", (repo_key(repo_id, sha),))

    # ---- reading -------------------------------------------------------------------------------

    def has_index(self, repo_id: str, commit_sha: str) -> bool:
        row = self.db.fetchone(
            "SELECT 1 FROM code_indexes WHERE repo_id = ? AND commit_sha = ?", (repo_id, commit_sha)
        )
        return row is not None

    def load_index(self, repo_id: str, commit_sha: str) -> CodeIndex | None:
        row = self.db.fetchone(
            "SELECT index_json FROM code_indexes WHERE repo_id = ? AND commit_sha = ?",
            (repo_id, commit_sha),
        )
        return CodeIndex.model_validate_json(row["index_json"]) if row else None

    def symbols(
        self,
        repo_id: str,
        commit_sha: str,
        *,
        path: str | None = None,
        kinds: Iterable[str] | None = None,
    ) -> list[Symbol]:
        """All symbols of an index (optionally one file / some kinds), in file then line order."""
        sql = f"SELECT {_SYMBOL_COLUMNS} FROM code_symbols WHERE repo_id = ? AND commit_sha = ?"
        params: list[object] = [repo_id, commit_sha]
        if path is not None:
            sql += " AND path = ?"
            params.append(path)
        if kinds is not None:
            kind_list = sorted(set(kinds))
            if not kind_list:
                return []
            sql += f" AND kind IN ({', '.join('?' * len(kind_list))})"
            params.extend(kind_list)
        sql += " ORDER BY path, start_line, end_line DESC, symbol_id"
        return [symbol_from_row(r) for r in self.db.fetchall(sql, params)]

    def symbol(self, repo_id: str, commit_sha: str, symbol_id: str) -> Symbol | None:
        row = self.db.fetchone(
            f"SELECT {_SYMBOL_COLUMNS} FROM code_symbols"
            " WHERE repo_id = ? AND commit_sha = ? AND symbol_id = ?",
            (repo_id, commit_sha, symbol_id),
        )
        return symbol_from_row(row) if row else None

    def symbols_by_id(self, repo_id: str, commit_sha: str, ids: Iterable[str]) -> dict[str, Symbol]:
        wanted = list(dict.fromkeys(ids))
        found: dict[str, Symbol] = {}
        for start in range(0, len(wanted), 500):  # stay well under SQLite's variable limit
            chunk = wanted[start : start + 500]
            rows = self.db.fetchall(
                f"SELECT {_SYMBOL_COLUMNS} FROM code_symbols WHERE repo_id = ? AND commit_sha = ?"
                f" AND symbol_id IN ({', '.join('?' * len(chunk))})",
                [repo_id, commit_sha, *chunk],
            )
            for r in rows:
                found[r["symbol_id"]] = symbol_from_row(r)
        return found

    def file_text(self, repo_id: str, commit_sha: str, path: str) -> str | None:
        row = self.db.fetchone(
            "SELECT text FROM code_files WHERE repo_id = ? AND commit_sha = ? AND path = ?",
            (repo_id, commit_sha, path),
        )
        return row["text"] if row else None

    def lines(self, repo_id: str, commit_sha: str, path: str, start: int, end: int) -> list[str]:
        """Lines start..end of a file (1-based, inclusive), clamped to the file. [] if unknown."""
        text = self.file_text(repo_id, commit_sha, path)
        if text is None or end < start:
            return []
        return split_lines(text)[max(start, 1) - 1 : max(end, 0)]

    def imports(self, repo_id: str, commit_sha: str, path: str | None = None) -> list[ImportRef]:
        sql = "SELECT path, line, module, names_json, alias FROM code_imports"
        sql += " WHERE repo_id = ? AND commit_sha = ?"
        params: list[object] = [repo_id, commit_sha]
        if path is not None:
            sql += " AND path = ?"
            params.append(path)
        return [
            ImportRef(
                path=r["path"],
                line=r["line"],
                module=r["module"],
                names=json.loads(r["names_json"]),
                alias=r["alias"],
            )
            for r in self.db.fetchall(sql + " ORDER BY path, line, rowid", params)
        ]

    def calls(self, repo_id: str, commit_sha: str, path: str | None = None) -> list[CallRef]:
        sql = "SELECT path, line, callee, in_symbol FROM code_calls"
        sql += " WHERE repo_id = ? AND commit_sha = ?"
        params: list[object] = [repo_id, commit_sha]
        if path is not None:
            sql += " AND path = ?"
            params.append(path)
        return [
            CallRef(path=r["path"], line=r["line"], callee=r["callee"], in_symbol=r["in_symbol"])
            for r in self.db.fetchall(sql + " ORDER BY path, line, rowid", params)
        ]
