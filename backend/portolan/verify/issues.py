"""Which findings count as issues on the map, and the counts that follow from them."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from portolan.models.finding import Finding
from portolan.store.chart import ChartStore


def is_issue(f: Finding, threshold: float) -> bool:
    """Only a confident mismatch is a node warning; uncertain and undocumented stay in the list."""
    return f.verdict == "mismatch" and f.confidence >= threshold


def findings_by_concept(findings: Iterable[Finding], threshold: float) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        if is_issue(f, threshold):
            for cid in f.concept_ids:
                out[cid].add(f.id)
    return out


def recount(
    chart: ChartStore, paper_id: str, findings: Iterable[Finding], threshold: float
) -> None:
    """Set `issue_count_self` on every concept from the findings (zero where there are none)."""
    counts = {cid: len(ids) for cid, ids in findings_by_concept(findings, threshold).items()}
    chart.set_issue_counts(paper_id, counts)
