"""Merge near-duplicate questions from different sources (spec §10.10: cluster near-duplicates).

Lexical only (Jaccard over content words), so it works with no embedding model installed.
"""

from __future__ import annotations

from dataclasses import dataclass

from portolan.faq.sources import RawItem
from portolan.faq.text import clip, jaccard, tokens

THRESHOLD = 0.55
# When sources collide, the one with the most direct evidence represents the cluster.
PRIORITY = {
    "finding": 0,
    "reviewer_discussion": 1,
    "github_issue": 2,
    "paper_page_discussion": 3,
    "anticipated": 4,
    "user_asked": 5,
}


@dataclass
class Cluster:
    rep: RawItem
    members: list[RawItem]

    @property
    def source_text(self) -> str:
        """Everything the sources said, the representative's first."""
        texts = [m.source_text for m in [self.rep, *self.members] if m.source_text]
        return clip("\n\n".join(dict.fromkeys(texts)), 2500)


def cluster_items(items: list[RawItem], threshold: float = THRESHOLD) -> list[Cluster]:
    """Greedy single-pass clustering, deterministic for a given input order."""
    ordered = sorted(items, key=lambda i: (PRIORITY.get(i.source_kind, 9), -i.weight, i.question))
    clusters: list[tuple[set[str], Cluster]] = []
    for item in ordered:
        words = set(tokens(item.question))
        for seen, cluster in clusters:
            if jaccard(words, seen) >= threshold:
                cluster.members.append(item)
                break
        else:
            clusters.append((words, Cluster(rep=item, members=[])))
    return [c for _, c in clusters]
