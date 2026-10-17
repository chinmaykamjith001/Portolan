"""Hybrid symbol retrieval over a stored code index (spec §10.4 steps 3-4).

BM25 over the FTS5 table, optionally fused with embedding cosine via reciprocal-rank fusion. Without
an embedder (or without stored vectors) the result is BM25 order. User text is never passed to MATCH
raw: it is cut into word tokens and each one is quoted (docs/notes/m3-externals.md §3).
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from portolan.code.embeddings import (
    Embedder,
    EmbeddingsUnavailable,
    has_embeddings,
    load_vectors,
)
from portolan.code.index_store import IndexStore, identifier_words, repo_key
from portolan.models.code import Symbol

CANDIDATES = 50  # BM25 hits and vector hits each considered before fusion
RRF_K = 60
MAX_TERMS = 48
# bm25() takes one weight per FTS column, in declaration order:
# symbol_id, repo_key (unindexed), name, name_parts, docstring, body, path.
_BM25_WEIGHTS = "0.0, 0.0, 5.0, 4.0, 2.0, 1.0, 0.5"
_TOKEN = re.compile(r"\w+")
_STOPWORDS = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on"}
    | {"or", "that", "the", "this", "to", "with"}
)


@dataclass
class Hit:
    symbol: Symbol
    score: float  # the ranking score: BM25 relevance alone, or the RRF score when fused
    bm25: float | None  # BM25 relevance with the sign flipped (higher is better); None if no match
    cosine: float | None  # cosine to the query vector; None when no vectors were used


Reranker = Callable[[str, list[Hit]], list[Hit]]


def query_terms(query: str) -> list[str]:
    """Lowercase search terms: each word, its identifier parts, joined forms of the whole query."""
    raw = [t.strip("_") for t in _TOKEN.findall(query)]
    raw = [t for t in raw if t]
    content = [t for t in raw if t.lower() not in _STOPWORDS] or raw
    terms: list[str] = []
    words: list[str] = []
    for token in content:
        parts = identifier_words(token) or [token.lower()]  # split before lowercasing: CamelCase
        terms.append(token.lower())
        terms.extend(parts)
        words.extend(parts)
    if 2 <= len(words) <= 6:
        # "scaled dot product attention" → scaled_dot_product_attention, and the lowercased form
        # FTS stores for ScaledDotProductAttention.
        terms.append("_".join(words))
        terms.append("".join(words))
    return list(dict.fromkeys(terms))[:MAX_TERMS]


def fts_query(query: str) -> str | None:
    """A MATCH expression that cannot be a syntax error: quoted terms, OR-ed, plus prefix forms."""
    clauses: list[str] = []
    for term in query_terms(query):
        quoted = '"' + term.replace('"', '""') + '"'
        clauses.append(quoted)
        if len(term) >= 3:
            clauses.append(quoted + "*")
    return " OR ".join(clauses) if clauses else None


def _bm25(
    store: IndexStore, repo_id: str, sha: str, match: str, kinds: set[str] | None
) -> list[tuple[str, float]]:
    sql = (
        f"SELECT code_fts.symbol_id AS symbol_id, bm25(code_fts, {_BM25_WEIGHTS}) AS rank"
        " FROM code_fts"
    )
    params: list[object] = []
    where = " WHERE code_fts MATCH ? AND code_fts.repo_key = ?"
    if kinds is not None:
        kind_list = sorted(kinds)
        sql += (
            " JOIN code_symbols s ON s.repo_id = ? AND s.commit_sha = ?"
            " AND s.symbol_id = code_fts.symbol_id"
        )
        params += [repo_id, sha]
        where += f" AND s.kind IN ({', '.join('?' * len(kind_list))})"
        params += [match, repo_key(repo_id, sha), *kind_list]
    else:
        params += [match, repo_key(repo_id, sha)]
    sql += where + " ORDER BY rank, code_fts.symbol_id LIMIT ?"
    params.append(CANDIDATES)
    try:
        rows = store.db.fetchall(sql, params)
    except sqlite3.OperationalError:  # belt and braces: fts_query should make this unreachable
        return []
    return [(r["symbol_id"], -float(r["rank"])) for r in rows]


def _dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def _query_vector(embedder: Embedder, query: str) -> list[float] | None:
    try:
        vectors = embedder.embed([query])
    except EmbeddingsUnavailable:
        return None
    if not vectors or len(vectors[0]) != embedder.dim:
        return None
    norm = math.sqrt(_dot(vectors[0], vectors[0]))
    return [x / norm for x in vectors[0]] if norm > 0 else None


def _cosines(
    store: IndexStore,
    repo_id: str,
    sha: str,
    query: str,
    embedder: Embedder,
    kinds: set[str] | None,
    bm25_ids: list[str],
) -> dict[str, float]:
    """Cosine for the BM25 candidates plus the top vector neighbours; {} when vectors are absent."""
    if not has_embeddings(store, repo_id, sha, embedder.model):
        return {}
    qv = _query_vector(embedder, query)
    if qv is None:
        return {}
    vectors = load_vectors(store, repo_id, sha, embedder.model, embedder.dim)
    if kinds is not None:
        allowed = {s.id for s in store.symbols(repo_id, sha, kinds=kinds)}
        vectors = {sid: v for sid, v in vectors.items() if sid in allowed}
    # Stored vectors are L2-normalised (bge normalises; HashEmbedder too), so dot = cosine.
    scored = {sid: _dot(qv, v) for sid, v in vectors.items()}
    related = [sid for sid, score in scored.items() if score > 0]  # no shared signal: not a hit
    top = sorted(related, key=lambda sid: (-scored[sid], sid))[:CANDIDATES]
    keep = set(top) | {sid for sid in bm25_ids if sid in scored}
    return {sid: scored[sid] for sid in keep}


def retrieve(
    store: IndexStore,
    repo_id: str,
    sha: str,
    query: str,
    k: int = 10,
    *,
    embedder: Embedder | None = None,
    kinds: set[str] | None = None,
    rerank: Reranker | None = None,
) -> list[Hit]:
    """The k symbols most relevant to `query`. Never raises on odd text; empty text gives []."""
    if k <= 0 or (kinds is not None and not kinds):
        return []
    match = fts_query(query)
    if match is None:
        return []

    lexical = _bm25(store, repo_id, sha, match, kinds)
    bm25_by_id = dict(lexical)
    cosine_by_id = (
        _cosines(store, repo_id, sha, query, embedder, kinds, [sid for sid, _ in lexical])
        if embedder is not None
        else {}
    )

    if cosine_by_id:
        fused: dict[str, float] = {}
        semantic = sorted(
            (sid for sid, c in cosine_by_id.items() if c > 0),
            key=lambda sid: (-cosine_by_id[sid], sid),
        )
        for ranking in ([sid for sid, _ in lexical], semantic):
            for rank, sid in enumerate(ranking, start=1):
                fused[sid] = fused.get(sid, 0.0) + 1.0 / (RRF_K + rank)
        order = sorted(fused, key=lambda sid: (-fused[sid], sid))
        scores = fused
    else:
        order = [sid for sid, _ in lexical]
        scores = bm25_by_id

    symbols = store.symbols_by_id(repo_id, sha, order)
    hits = [
        Hit(
            symbol=symbols[sid],
            score=scores[sid],
            bm25=bm25_by_id.get(sid),
            cosine=cosine_by_id.get(sid),
        )
        for sid in order
        if sid in symbols
    ]

    # ---- HOOK: LLM rerank (spec §10.4 step 4) ---------------------------------------------------
    # A later milestone passes a reranker that asks the LLM (through llm/gateway.py) to reorder the
    # fused candidates. It receives every candidate, not just the top k, and returns them reordered
    # (it may drop some). Nothing here calls an LLM.
    if rerank is not None:
        hits = rerank(query, hits)
    # ----------------------------------------------------------------------------------------------

    return hits[:k]
