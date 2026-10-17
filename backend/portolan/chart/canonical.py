"""Concept-name canonicalization and within-paper dedupe (spec §10.5.3).

Conservative by design: an exact canonical match reuses a concept; a near match is only reused
when the judge (a `fast` LLM call supplied by the caller) agrees. "LayerNorm" and "RMSNorm" stay
distinct.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Awaitable, Callable

from portolan.models.concept import Concept
from portolan.store.chart import ChartStore

Judge = Callable[[tuple[str, str], tuple[str, str]], Awaitable[bool]]

_ARTICLES = ("the", "a", "an")
_KEEP_ENDINGS = ("ss", "us", "is", "as", "os")
_KEEP_WORDS = {
    "series",
    "species",
    "physics",
    "mathematics",
    "statistics",
    "dynamics",
    "news",
    "lens",
}
_IRREGULAR = {
    "matrices": "matrix",
    "vertices": "vertex",
    "indices": "index",
    "appendices": "appendix",
    "axes": "axis",
    "analyses": "analysis",
    "hypotheses": "hypothesis",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "data": "data",
    "relus": "relu",
    "gelus": "gelu",
}
# Applied on token boundaries after singularization; longest phrases win.
_ALIASES = {
    "layernorm": "layer normalization",
    "layer norm": "layer normalization",
    "batchnorm": "batch normalization",
    "batch norm": "batch normalization",
    "rmsnorm": "rms normalization",
    "rms norm": "rms normalization",
    "root mean square normalization": "rms normalization",
    "root mean square layer normalization": "rms normalization",
    "relu": "rectified linear unit",
    "gelu": "gaussian error linear unit",
    "mlp": "multilayer perceptron",
    "multi layer perceptron": "multilayer perceptron",
    "ffn": "feed forward network",
    "feedforward network": "feed forward network",
    "selfattention": "self attention",
    "sgd": "stochastic gradient descent",
}
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ALIASES, key=len, reverse=True)) + r")\b"
)
SLUG_MAX = 60


def _singularize(word: str) -> str:
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if len(word) <= 3 or word in _KEEP_WORDS or word.endswith(_KEEP_ENDINGS):
        return word
    if word.endswith("ices") and len(word) > 5:
        return word[:-4] + "ix"
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def canonical_name(title: str) -> str:
    s = title.lower().replace("'", "").replace("\N{RIGHT SINGLE QUOTATION MARK}", "")
    s = re.sub(r"[^\w\s]|_", " ", s)  # punctuation, incl. internal hyphens, becomes a space
    tokens = s.split()
    while len(tokens) > 1 and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    if not tokens:
        return ""
    tokens[-1] = _singularize(tokens[-1])
    s = " ".join(tokens)
    return _ALIAS_RE.sub(lambda m: _ALIASES[m.group(1)], s)


def concept_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-")
    return slug or "concept"


def similarity(a: str, b: str) -> float:
    """0..1: the mean of token Jaccard and difflib ratio, computed on canonical names."""
    ca, cb = canonical_name(a), canonical_name(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    ta, tb = set(ca.split()), set(cb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = difflib.SequenceMatcher(None, ca, cb).ratio()
    return (jaccard + ratio) / 2


class Canonicalizer:
    """Resolves new concept titles to existing concept ids for one paper.

    Concepts produced earlier in the same run can be `register`ed before they are stored, so a
    batch that names the same concept twice yields one id.
    """

    MAX_JUDGED = 3  # judge at most this many near candidates per title (each is an LLM call)

    def __init__(
        self,
        store: ChartStore,
        paper_id: str,
        judge: Judge | None = None,
        threshold: float = 0.82,
    ) -> None:
        self.store = store
        self.paper_id = paper_id
        self.judge = judge
        self.threshold = threshold
        self._registered: dict[str, Concept] = {}
        self._issued: set[str] = set()

    def register(self, concept: Concept) -> None:
        self._registered[concept.id] = concept
        self._issued.add(concept.id)

    def _known(self) -> dict[str, Concept]:
        known = self.store.concepts(self.paper_id)
        known.update(self._registered)
        return known

    async def resolve(self, title: str, gist: str) -> str | None:
        canon = canonical_name(title)
        if not canon:
            return None
        for c in self._registered.values():
            if c.canonical_name == canon:
                return c.id
        stored = self.store.find_by_canonical(self.paper_id, canon)
        if stored is not None:
            return stored.id
        if self.judge is None:
            return None
        scored: list[tuple[float, str, Concept]] = []
        for c in self._known().values():
            score = similarity(canon, c.canonical_name or c.title)
            if score >= self.threshold:
                scored.append((score, c.id, c))
        scored.sort(key=lambda t: (-t[0], t[1]))
        for _score, _id, c in scored[: self.MAX_JUDGED]:
            if await self.judge((title, gist), (c.title, c.gist)):
                return c.id
        return None

    def new_id(self, title: str) -> str:
        base = concept_slug(title)
        candidate, n = base, 1
        while candidate in self._issued or self.store.get_concept(self.paper_id, candidate):
            n += 1
            suffix = f"-{n}"
            candidate = base[: SLUG_MAX - len(suffix)].rstrip("-") + suffix
        self._issued.add(candidate)
        return candidate
