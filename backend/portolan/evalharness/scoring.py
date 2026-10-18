"""Recall and precision for paper-vs-code discrepancy detection.

MATCHING RULE (ours; replace it if the official SciCoQA scorer differs, see eval/README.md):

* A *prediction* is a finding whose verdict is in `POSITIVE_VERDICTS` (`mismatch`,
  `undocumented_in_paper`). `uncertain`, `not_found` and `matches` are not predictions: Portolan
  surfaces a mismatch only above its confidence threshold (§10.9.5).
* A prediction matches a gold discrepancy when
  1. **code location**: one of its code spans is in the gold file (path equality, or one path is a
     suffix of the other at a `/` boundary) and its line range comes within `LINE_TOLERANCE` lines
     of the gold range (a gold row with a file but no lines can't match this way); or, failing that,
  2. **description similarity**: the Dice coefficient of the content-word sets of the gold
     description and the finding's claim + evidence is at least `TEXT_THRESHOLD` and they share at
     least `MIN_SHARED_WORDS` words. No LLM is involved.
* Matching is one-to-one (greedy, best score first): a finding explains at most one gold
  discrepancy and vice versa.
* recall = matched gold / all gold; precision = matched predictions / all predictions, both over
  the records that finished. Precision only counts a prediction as correct if it matches a LISTED
  discrepancy, so it is a lower bound when the gold list is incomplete.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from portolan.evalharness.checkpoint import RecordResult
from portolan.evalharness.records import Discrepancy, EvalRecord
from portolan.models.finding import Finding

POSITIVE_VERDICTS = frozenset({"mismatch", "undocumented_in_paper"})
LINE_TOLERANCE = 5
TEXT_THRESHOLD = 0.35
MIN_SHARED_WORDS = 2
_STOP_TEXT = (
    "the a an and or of to in on for with is are was were be been this that these those it its as "
    "by at from but not no than then so if when which while into over under per via also uses use "
    "used code paper implementation instead rather does do did has have had will would should can "
    "may might"
)
STOPWORDS = frozenset(_STOP_TEXT.split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {_stem(w) for w in words if len(w) >= 3 and w not in STOPWORDS}


def dice(a: set[str], b: set[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def same_file(a: str, b: str) -> bool:
    x, y = a.replace("\\", "/").lstrip("./"), b.replace("\\", "/").lstrip("./")
    return x == y or x.endswith("/" + y) or y.endswith("/" + x)


class Matcher(Protocol):
    name: str
    description: str

    def score(self, gold: Discrepancy, finding: Finding) -> float | None:
        """None = no match; otherwise higher is a better match (used for one-to-one assignment)."""
        ...


class DefaultMatcher:
    name = "location-or-text"
    description = (
        f"code span in the gold file within {LINE_TOLERANCE} lines of the gold range, or "
        f"content-word Dice ≥ {TEXT_THRESHOLD} (≥ {MIN_SHARED_WORDS} shared words); "
        "one-to-one, best first."
    )

    def __init__(
        self,
        line_tolerance: int = LINE_TOLERANCE,
        text_threshold: float = TEXT_THRESHOLD,
        min_shared: int = MIN_SHARED_WORDS,
    ) -> None:
        self.line_tolerance = line_tolerance
        self.text_threshold = text_threshold
        self.min_shared = min_shared

    def _by_location(self, gold: Discrepancy, finding: Finding) -> bool:
        if gold.code_file is None or gold.code_start is None:
            return False
        start = gold.code_start
        end = gold.code_end if gold.code_end is not None else start
        return any(
            same_file(span.path, gold.code_file)
            and span.start_line <= end + self.line_tolerance
            and span.end_line >= start - self.line_tolerance
            for span in finding.code_spans
        )

    def score(self, gold: Discrepancy, finding: Finding) -> float | None:
        g, f = (
            content_words(gold.description),
            content_words(f"{finding.claim_text} {finding.evidence}"),
        )
        sim = dice(g, f)
        if self._by_location(gold, finding):
            return 1.0 + sim
        if sim >= self.text_threshold and len(g & f) >= self.min_shared:
            return sim
        return None


@dataclass
class Match:
    gold_id: str
    finding_id: str
    score: float


@dataclass
class RecordScore:
    record_id: str
    matches: list[Match] = field(default_factory=list[Match])
    missed: list[str] = field(default_factory=list[str])  # gold ids
    false_positives: list[str] = field(default_factory=list[str])  # finding ids
    n_gold: int = 0
    n_predictions: int = 0
    n_uncertain: int = 0


@dataclass
class Tally:
    hit: int = 0
    total: int = 0

    @property
    def rate(self) -> float | None:
        return self.hit / self.total if self.total else None


@dataclass
class Scores:
    records: list[RecordScore] = field(default_factory=list[RecordScore])
    gold: Tally = field(default_factory=Tally)  # hit = matched gold
    predictions: Tally = field(default_factory=Tally)  # hit = matched predictions
    recall_by_category: dict[str, Tally] = field(default_factory=dict[str, Tally])
    recall_by_split: dict[str, Tally] = field(default_factory=dict[str, Tally])
    precision_by_claim_type: dict[str, Tally] = field(default_factory=dict[str, Tally])
    matched_by_method: dict[str, int] = field(default_factory=dict[str, int])
    uncertain: int = 0

    @property
    def recall(self) -> float | None:
        return self.gold.rate

    @property
    def precision(self) -> float | None:
        return self.predictions.rate


def assign(gold: list[Discrepancy], findings: list[Finding], matcher: Matcher) -> list[Match]:
    """One-to-one greedy assignment, best score first; ties break on ids for determinism."""
    scored: list[tuple[float, str, str]] = []
    for g in gold:
        for f in findings:
            s = matcher.score(g, f)
            if s is not None:
                scored.append((s, g.id, f.id))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_gold: set[str] = set()
    used_find: set[str] = set()
    out: list[Match] = []
    for s, gid, fid in scored:
        if gid in used_gold or fid in used_find:
            continue
        used_gold.add(gid)
        used_find.add(fid)
        out.append(Match(gold_id=gid, finding_id=fid, score=s))
    return out


def score_records(
    records: list[EvalRecord],
    results: dict[str, RecordResult],
    matcher: Matcher | None = None,
    *,
    positive_verdicts: frozenset[str] = POSITIVE_VERDICTS,
) -> Scores:
    """Score the records that finished (`status == "done"`); others are left out entirely."""
    matcher = matcher or DefaultMatcher()
    out = Scores()
    cat: dict[str, Tally] = defaultdict(Tally)
    split: dict[str, Tally] = defaultdict(Tally)
    ctype: dict[str, Tally] = defaultdict(Tally)
    for rec in records:
        res = results.get(rec.id)
        if res is None or res.status != "done":
            continue
        preds = [f for f in res.findings if f.verdict in positive_verdicts]
        matches = assign(rec.gold, preds, matcher)
        matched_gold = {m.gold_id for m in matches}
        matched_find = {m.finding_id for m in matches}
        by_find = {f.id: f for f in preds}
        rs = RecordScore(
            record_id=rec.id,
            matches=matches,
            missed=[g.id for g in rec.gold if g.id not in matched_gold],
            false_positives=[f.id for f in preds if f.id not in matched_find],
            n_gold=len(rec.gold),
            n_predictions=len(preds),
            n_uncertain=sum(f.verdict == "uncertain" for f in res.findings),
        )
        out.records.append(rs)
        out.gold.total += len(rec.gold)
        out.gold.hit += len(matches)
        out.predictions.total += len(preds)
        out.predictions.hit += len(matches)
        out.uncertain += rs.n_uncertain
        for g in rec.gold:
            hit = g.id in matched_gold
            for tally in (cat[g.category or "unlabeled"], split[g.split]):
                tally.total += 1
                tally.hit += hit
        for f in preds:
            t = ctype[f.claim_type]
            t.total += 1
            t.hit += f.id in matched_find
        for m in matches:
            method = by_find[m.finding_id].method
            out.matched_by_method[method] = out.matched_by_method.get(method, 0) + 1
    out.recall_by_category = dict(sorted(cat.items()))
    out.recall_by_split = dict(sorted(split.items()))
    out.precision_by_claim_type = dict(sorted(ctype.items()))
    return out
