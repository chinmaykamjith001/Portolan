"""Attach a finding to the concept it is about (spec §10.9.6): anchor overlap, then words."""

from __future__ import annotations

from portolan.chart.issues import nearest_ancestor_by_anchor
from portolan.models.concept import MASTER, Concept
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore
from portolan.verify.values import canon_words

MIN_SHARED_WORDS = 2


class ConceptMapper:
    """Loads the paper's concepts once; maps many findings."""

    def __init__(self, doc: PaperDoc, chart: ChartStore) -> None:
        self.doc = doc
        self.chart = chart
        self.concepts: dict[str, Concept] = chart.concepts(doc.paper_id)
        self._parents: dict[str, str | None] = {}

    def _parent(self, concept_id: str) -> str | None:
        if concept_id not in self._parents:
            layers = [
                lid
                for lid in self.chart.layers_containing(self.doc.paper_id, concept_id)
                if lid != concept_id
            ]
            real = [lid for lid in layers if lid != MASTER]
            self._parents[concept_id] = real[0] if real else None
        return self._parents[concept_id]

    def _anchor_set(self, anchor_id: str) -> set[str]:
        out: set[str] = set()
        node = self.doc.anchor(anchor_id)
        while node is not None and node.id not in out:
            out.add(node.id)
            node = self.doc.anchor(node.parent_id) if node.parent_id else None
        return out

    def map(self, anchor_id: str, text: str) -> list[str]:
        """[concept id] (the deepest generated concept that fits) or [] when nothing fits."""
        if not self.concepts:
            return []
        if anchor_id:
            hit = nearest_ancestor_by_anchor(
                self._anchor_set(anchor_id), self.concepts, self._parent
            )
            if hit is not None:
                return [hit]
        words = canon_words(text)
        best: tuple[int, int, str] | None = None
        for cid, c in self.concepts.items():
            shared = len(words & canon_words(f"{c.title} {c.gist}"))
            if shared < MIN_SHARED_WORDS:
                continue
            key = (-shared, -c.depth, cid)
            if best is None or key < best:
                best = key
        return [best[2]] if best else []
