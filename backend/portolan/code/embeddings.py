"""Symbol embeddings for hybrid retrieval (spec §10.4 step 3).

The real model (BAAI/bge-small-en-v1.5 via sentence-transformers) is an optional extra: about 750 MB
installed and ~29 s to import (docs/notes/m3-externals.md §2). It is therefore imported lazily, only
when a vector is actually needed, and never at module import. Without it retrieval is BM25-only.
`HashEmbedder` is a deterministic stand-in for tests.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import math
import struct
from typing import Any, Protocol

from portolan.code.index_store import IndexStore, identifier_words, split_lines, symbol_body
from portolan.models.code import Symbol

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_TEXT_CHARS = 1_500  # bge truncates at 512 tokens anyway


class EmbeddingsUnavailable(Exception):
    """The optional `embeddings` extra (sentence-transformers) is not installed."""


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm > 0 else vector


class HashEmbedder:
    """Deterministic hashed bag of identifier words, L2-normalised. Lexical, not semantic."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model = f"hash-bow-{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for word in identifier_words(text):
                digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
                h = int.from_bytes(digest, "little")
                vector[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
            out.append(_normalise(vector))
        return out


class SentenceTransformerEmbedder:
    """bge-small-en-v1.5 (384-d, CLS pooling + normalise) through sentence-transformers."""

    def __init__(self, model: str = DEFAULT_MODEL, dim: int = 384) -> None:
        if importlib.util.find_spec("sentence_transformers") is None:
            raise EmbeddingsUnavailable(
                "sentence-transformers is not installed; run `uv sync --extra embeddings`"
            )
        self.model = model
        self.dim = dim
        self._st: Any = None  # loaded on the first embed() call: the import alone takes ~29 s

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._st is None:
            try:
                module = importlib.import_module("sentence_transformers")
            except ImportError as exc:
                raise EmbeddingsUnavailable(str(exc)) from exc
            self._st = module.SentenceTransformer(self.model)
        vectors: Any = self._st.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [[float(x) for x in row] for row in vectors]


def default_embedder() -> Embedder | None:
    """The real embedder if the extra is installed, else None. Cheap: imports nothing heavy."""
    try:
        return SentenceTransformerEmbedder()
    except EmbeddingsUnavailable:
        return None


# ---- storage -------------------------------------------------------------------------------------


def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def symbol_text(symbol: Symbol, lines: list[str]) -> str:
    """What gets embedded for a symbol: names, signature, docstring and the start of its body."""
    parts = [
        f"{symbol.kind} {symbol.qualname or symbol.name}",
        " ".join(identifier_words(symbol.name)),
        symbol.signature or "",
        symbol.docstring or "",
        symbol.value or "",
        symbol_body(lines, symbol),
    ]
    return "\n".join(p for p in parts if p)[:EMBED_TEXT_CHARS]


def embed_symbols(
    store: IndexStore, repo_id: str, commit_sha: str, embedder: Embedder, batch: int = 64
) -> int:
    """(Re)compute and store a vector for every symbol of an index. Returns how many were stored."""
    symbols = store.symbols(repo_id, commit_sha)
    lines_by_path: dict[str, list[str]] = {}
    texts: list[str] = []
    for s in symbols:
        if s.path not in lines_by_path:
            lines_by_path[s.path] = split_lines(store.file_text(repo_id, commit_sha, s.path) or "")
        texts.append(symbol_text(s, lines_by_path[s.path]))

    rows: list[tuple[object, ...]] = []
    for start in range(0, len(texts), max(batch, 1)):
        chunk = symbols[start : start + batch]
        vectors = embedder.embed(texts[start : start + batch])
        for s, v in zip(chunk, vectors, strict=True):
            if len(v) != embedder.dim:
                raise ValueError(
                    f"{embedder.model} returned {len(v)} dims, expected {embedder.dim}"
                )
            rows.append((repo_id, commit_sha, s.id, embedder.model, embedder.dim, pack_vector(v)))

    with store.db.transaction() as conn:
        conn.execute(
            "DELETE FROM code_embeddings WHERE repo_id = ? AND commit_sha = ? AND model = ?",
            (repo_id, commit_sha, embedder.model),
        )
        conn.executemany(
            "INSERT INTO code_embeddings (repo_id, commit_sha, symbol_id, model, dim, vector)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def has_embeddings(store: IndexStore, repo_id: str, commit_sha: str, model: str) -> bool:
    row = store.db.fetchone(
        "SELECT 1 FROM code_embeddings WHERE repo_id = ? AND commit_sha = ? AND model = ? LIMIT 1",
        (repo_id, commit_sha, model),
    )
    return row is not None


def load_vectors(
    store: IndexStore, repo_id: str, commit_sha: str, model: str, dim: int
) -> dict[str, list[float]]:
    """symbol_id → vector for one model; rows of another dimension are ignored."""
    rows = store.db.fetchall(
        "SELECT symbol_id, vector FROM code_embeddings"
        " WHERE repo_id = ? AND commit_sha = ? AND model = ? AND dim = ?",
        (repo_id, commit_sha, model, dim),
    )
    return {r["symbol_id"]: unpack_vector(r["vector"], dim) for r in rows}
