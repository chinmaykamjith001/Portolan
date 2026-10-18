"""Small lexical helpers shared by clustering, anchor retrieval and concept linking."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from portolan.models.paper import Anchor, PaperDoc

_WORD = re.compile(r"[a-z0-9]+")
_STOP_TEXT = (
    "a an and are as at be been but by can could did do does for from had has have how i "
    "if in into is it its me my of on or our paper should so than that the their then "
    "there these they this those to was we were what when where which who why will with "
    "would you your"
)
STOPWORDS = frozenset(_STOP_TEXT.split())


def _stem(w: str) -> str:
    """Crude suffix folding so "divide"/"divided"/"divides" and "head"/"heads" compare equal."""
    if len(w) >= 4:
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and not w.endswith("ss") and len(w) - len(suffix) >= 2:
                w = w[: -len(suffix)]
                break
    if len(w) >= 3 and w.endswith("e"):
        w = w[:-1]
    return w


# Notation people type vs. the words papers write: "divided by sqrt(d_k)" must find "we scale by
# the square root of d_k". Applied to queries and texts alike.
_SPELLED = {
    "√": " square root ", "sqrt": " square root ", "softmax": " softmax ", "argmax": " argmax ",
    "lr": " learning rate ", "dim": " dimension ", "dims": " dimension ", "eq": " equation ",
    "params": " parameters ", "param": " parameter ", "num": " number ",
}  # fmt: skip
_SPELL = re.compile(r"√|\b(?:sqrt|lr|dims?|eq|params?|num)\b")


def tokens(text: str) -> list[str]:
    """Lowercase, stemmed content words."""
    text = _SPELL.sub(lambda m: _SPELLED[m.group(0)], text.lower())
    return [_stem(w) for w in _WORD.findall(text) if w not in STOPWORDS and len(w) >= 2]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("\x1f".join(parts).encode(), usedforsecurity=False).hexdigest()
    return f"{prefix}-{digest[:10]}"


def rank_texts(query: str, texts: list[str], k: int, *, min_hits: int = 1) -> list[int]:
    """Indexes of the `k` texts best matching `query` (idf-weighted term overlap).

    Texts sharing fewer than `min_hits` distinct query terms are not returned at all, so a
    caller can tell "nothing relevant" from "weak matches".
    """
    q = set(tokens(query))
    if not q or not texts:
        return []
    docs = [Counter(tokens(t)) for t in texts]
    n = len(docs)
    scored: list[tuple[float, int]] = []
    for i, counts in enumerate(docs):
        shared = q & counts.keys()
        if len(shared) < min_hits:
            continue
        score = 0.0
        for term in shared:
            df = sum(1 for d in docs if term in d)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            tf = counts[term]
            score += idf * (tf * 2.2) / (tf + 1.2)
        scored.append((score, i))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [i for _, i in scored[:k]]


def anchor_passages(doc: PaperDoc, query: str, k: int = 3, *, min_hits: int = 1) -> list[Anchor]:
    """Paper anchors (with text) that best match `query`.

    An equation rarely explains itself: the paragraphs right before and after it (same parent)
    usually say why it looks the way it does, so a matched equation brings them along."""
    pool = [a for a in doc.anchors if a.text and a.text.strip()]
    hits = [pool[i] for i in rank_texts(query, [a.text for a in pool], k, min_hits=min_hits)]
    ordered = sorted(pool, key=lambda a: a.order)
    position = {a.id: i for i, a in enumerate(ordered)}
    out: list[Anchor] = []
    for a in hits:
        if a not in out:
            out.append(a)
        if a.kind != "equation":
            continue
        i = position[a.id]
        for j in (i + 1, i - 1):
            if 0 <= j < len(ordered):
                near = ordered[j]
                if near.kind == "paragraph" and near.parent_id == a.parent_id and near not in out:
                    out.append(near)
    return out[: max(k, len(hits))]


def passage(a: Anchor, limit: int = 1200) -> dict[str, str]:
    return {"id": a.id, "label": a.label, "text": a.text[:limit]}


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
