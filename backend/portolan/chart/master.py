"""Master layer and skeleton generation (spec §8 `chart.master` / `chart.skeleton`, §10.5).

LLM output is never trusted structurally: anchors must exist in the paper, link endpoints must be
known concepts, self-loops and duplicates are dropped, and concepts that are really section names
are reported (and dropped from the master layer).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from portolan.chart.canonical import Canonicalizer, canonical_name
from portolan.llm.gateway import Gateway
from portolan.llm.prompts import canonical_same_concept, master_layer, skeleton_batch
from portolan.llm.prompts.chart_schemas import (
    ConceptOut,
    LinkOut,
    MasterLayerOut,
    SameConceptOut,
    SkeletonBatchOut,
)
from portolan.llm.types import Priority
from portolan.models.concept import MASTER, Concept, Grounding, Layer, Link
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore

CENTER = "center"
DEFAULT_BATCH = 6
DEFAULT_MAX_DEPTH = 8
GENERIC_SECTION_WORDS = {
    "introduction", "background", "related work", "method", "methods", "experiment", "experiments",
    "result", "results", "discussion", "conclusion", "conclusions", "training", "evaluation",
    "setup", "appendix", "acknowledgment", "acknowledgement", "abstract", "model", "approach",
}  # fmt: skip


@dataclass
class ChartBuild:
    layers: list[Layer] = field(default_factory=list[Layer])
    warnings: list[str] = field(default_factory=list[str])
    requests: int = 0  # gateway calls that missed the cache


Judge = Callable[[tuple[str, str], tuple[str, str]], Awaitable[bool]]


def _clean(text: str, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _section_names(doc: PaperDoc) -> set[str]:
    """Structural section names that must not become concepts ("Results", "Training setup").

    A subsection titled after an idea ("Label Smoothing", "Scaled Dot-Product Attention") is a
    legitimate concept: the spec's own examples include them. So only generic structural names
    count, plus paper section titles made only of generic words ("Experimental Setup").
    """
    generic_words = {w for name in GENERIC_SECTION_WORDS for w in name.split()} | {
        "experimental", "detail", "details", "implementation", "analysis", "future", "work",
        "limitation", "limitations", "and", "of", "the",
    }  # fmt: skip
    structural = {
        canonical_name(a.text)
        for a in doc.by_kind("section")
        if set(canonical_name(a.text).split()) <= generic_words
    }
    return structural | GENERIC_SECTION_WORDS


def _grounding(guess: str) -> Grounding:
    return "unknown" if guess not in {"in_code", "in_library", "concept_only"} else guess  # type: ignore[return-value]


MAX_MASTER = 20
MAX_CHILDREN = 10


def _norm_key(key: str) -> str:
    """Link ends as models write them: "Self-Attention", " self attention", "self_attention"."""
    return "-".join(key.strip().lower().replace("_", " ").split())


def _remember(key_to_id: dict[str, str], c: ConceptOut, cid: str) -> None:
    """Make a concept reachable by its key and, failing that, its title."""
    key_to_id[_norm_key(c.key)] = cid
    key_to_id.setdefault(_norm_key(c.title), cid)


def _links(
    layer_id: str, raw: Iterable[LinkOut], key_to_id: dict[str, str], warnings: list[str]
) -> list[Link]:
    links: list[Link] = []
    seen: set[tuple[str, str]] = set()
    for link in raw:
        source = key_to_id.get(_norm_key(link.source))
        target = key_to_id.get(_norm_key(link.target))
        if source is None or target is None:
            warnings.append(
                f"{layer_id}: dropped link with unknown end ({link.source} → {link.target})"
            )
            continue
        label = _clean(link.label, 60)
        if source == target or (source, target) in seen or not label:
            continue
        seen.add((source, target))
        links.append(
            Link(
                id=f"{layer_id}:{source}->{target}",
                layer_id=layer_id,
                source=source,
                target=target,
                label=label,
                kind=link.kind,
            )
        )
    return links


class ChartBuilder:
    def __init__(
        self,
        doc: PaperDoc,
        gateway: Gateway,
        store: ChartStore,
        *,
        batch_size: int = DEFAULT_BATCH,
        max_depth: int = DEFAULT_MAX_DEPTH,
        use_judge: bool = True,
    ) -> None:
        self.doc = doc
        self.gateway = gateway
        self.store = store
        self.batch_size = batch_size
        self.max_depth = max_depth
        self.anchor_ids = {a.id for a in doc.anchors}
        self.canon = Canonicalizer(store, doc.paper_id, judge=self._judge if use_judge else None)
        self.build = ChartBuild()

    # -- LLM helpers ----------------------------------------------------------------------

    async def _call(self, role: Any, template: Any, inputs: dict[str, Any], schema: Any,
                    priority: Priority) -> Any:  # fmt: skip
        result = await self.gateway.call(
            role, template, inputs, schema, priority, self.doc.paper_id
        )
        if not result.cache_hit:
            self.build.requests += 1
        return result.parsed

    async def _judge(self, a: tuple[str, str], b: tuple[str, str]) -> bool:
        out: SameConceptOut = await self._call(
            "fast",
            canonical_same_concept.TEMPLATE,
            {"a_title": a[0], "a_gist": a[1], "b_title": b[0], "b_gist": b[1]},
            SameConceptOut,
            "P1",
        )
        return out.same

    def _anchors(self, ids: Iterable[str]) -> list[str]:
        return [a for a in dict.fromkeys(ids) if a in self.anchor_ids]

    async def _concept(self, out: ConceptOut, depth: int) -> tuple[str, bool]:
        """Resolve to an existing concept (shared node) or create one. Returns (id, is_new)."""
        title = _clean(out.title, 80)
        existing = await self.canon.resolve(title, out.gist)
        if existing is not None:
            concept = self.store.get_concept(self.doc.paper_id, existing)
            if concept is not None and depth < concept.depth:
                self.store.upsert_concept(
                    self.doc.paper_id, concept.model_copy(update={"depth": depth})
                )
            return existing, False
        concept = Concept(
            id=self.canon.new_id(title),
            canonical_name=canonical_name(title),
            title=title,
            gist=_clean(out.gist, 200),
            grounding=_grounding(out.grounding_guess),
            is_first_principle=out.is_first_principle,
            depth=depth,
            anchors=self._anchors(out.anchors),
        )
        self.canon.register(concept)
        self.store.upsert_concept(self.doc.paper_id, concept)
        return concept.id, True

    # -- master layer ---------------------------------------------------------------------

    async def master(self, priority: Priority = "P0") -> Layer:
        existing = self.store.get_layer(self.doc.paper_id, MASTER)
        if existing is not None:
            return existing
        out: MasterLayerOut = await self._call(
            "strong", master_layer.TEMPLATE, master_layer.build_inputs(self.doc), MasterLayerOut,
            priority,
        )  # fmt: skip
        warnings = self.build.warnings
        center = Concept(
            id=self.canon.new_id(out.center.title),
            canonical_name=canonical_name(out.center.title),
            title=_clean(out.center.title, 80),
            gist=_clean(out.center.gist, 200),
            depth=0,
            anchors=self._anchors(out.center.anchors),
            has_layer=True,
        )
        self.canon.register(center)
        self.store.upsert_concept(self.doc.paper_id, center)

        sections = _section_names(self.doc)
        key_to_id: dict[str, str] = {
            _norm_key(CENTER): center.id,
            _norm_key(center.title): center.id,
        }
        members: list[str] = []
        for c in out.concepts:
            if len(members) >= MAX_MASTER:
                warnings.append(f"master: kept the first {MAX_MASTER} concepts")
                break
            if canonical_name(c.title) in sections:
                warnings.append(f"master: dropped section-name concept {c.title!r}")
                continue
            cid, _ = await self._concept(c, depth=1)
            _remember(key_to_id, c, cid)  # before de-duplication, so links using this key resolve
            if cid == center.id or cid in members:
                continue
            members.append(cid)
        if not 10 <= len(members) <= 20:
            warnings.append(f"master: {len(members)} concepts (target 10-20)")
        layer = Layer(
            id=MASTER,
            paper_id=self.doc.paper_id,
            center=center.id,
            concepts=members,
            links=_links(MASTER, out.links, key_to_id, warnings),
        )
        self.store.save_layer(layer)
        self.build.layers.append(layer)
        return layer

    # -- skeletons ------------------------------------------------------------------------

    def path_to(self, concept_id: str) -> list[str]:
        """Titles from the master center down to this concept (shallowest parent each step)."""
        titles: list[str] = []
        seen: set[str] = set()
        current: str | None = concept_id
        while current is not None and current not in seen:
            seen.add(current)
            concept = self.store.get_concept(self.doc.paper_id, current)
            if concept is None:
                break
            titles.append(concept.title)
            parents = [
                self.store.get_layer(self.doc.paper_id, lid)
                for lid in self.store.layers_containing(self.doc.paper_id, current)
            ]
            centers = [p.center for p in parents if p is not None and p.center != current]
            ranked = sorted(
                (c for c in (self.store.get_concept(self.doc.paper_id, x) for x in centers) if c),
                key=lambda c: c.depth,
            )
            current = ranked[0].id if ranked else None
        return list(reversed(titles))

    def needs_skeleton(self, concept: Concept) -> bool:
        return (
            not concept.is_first_principle
            and concept.depth < self.max_depth
            and self.store.get_layer(self.doc.paper_id, concept.id) is None
        )

    def _item(self, concept: Concept) -> dict[str, Any]:
        texts: list[str] = []
        for aid in concept.anchors[:3]:
            anchor = self.doc.anchor(aid)
            if anchor is not None:
                texts.append(_clean(anchor.text, 400))
        return {
            "key": concept.id,
            "title": concept.title,
            "gist": concept.gist,
            "path": self.path_to(concept.id),
            "anchor_texts": texts,
        }

    async def skeletons(self, concept_ids: Iterable[str], priority: Priority = "P1") -> list[Layer]:
        """Generate child layers (skeleton only) for these concepts, batched."""
        todo = [
            c
            for c in (
                self.store.get_concept(self.doc.paper_id, cid) for cid in dict.fromkeys(concept_ids)
            )
            if c is not None and self.needs_skeleton(c)
        ]
        made: list[Layer] = []
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            inputs = {
                "paper_title": self.doc.title,
                "paper_summary": _clean(self.doc.abstract, 600),
                "min_children": 4,
                "max_children": 10,
                "items": [self._item(c) for c in batch],
            }
            out: SkeletonBatchOut = await self._call(
                "fast", skeleton_batch.TEMPLATE, inputs, SkeletonBatchOut, priority
            )
            by_key = {c.id: c for c in batch}
            for raw in out.layers:
                parent = by_key.pop(raw.center_key, None)
                if parent is None:
                    self.build.warnings.append(f"skeleton: unknown center_key {raw.center_key!r}")
                    continue
                made.append(await self._child_layer(parent, raw.concepts, raw.links))
            for missing in by_key.values():
                self.build.warnings.append(f"skeleton: no layer returned for {missing.id}")
        return made

    async def _child_layer(
        self, parent: Concept, concepts: list[ConceptOut], links: list[LinkOut]
    ) -> Layer:
        key_to_id: dict[str, str] = {
            _norm_key(CENTER): parent.id,
            _norm_key(parent.title): parent.id,
        }
        ancestors = {canonical_name(t) for t in self.path_to(parent.id)}
        members: list[str] = []
        for c in concepts:
            if len(members) >= MAX_CHILDREN:
                break
            if canonical_name(c.title) in ancestors:
                continue  # a layer never contains its own center or ancestors
            cid, _ = await self._concept(c, depth=parent.depth + 1)
            _remember(key_to_id, c, cid)
            if cid == parent.id or cid in members:
                continue
            members.append(cid)
        layer = Layer(
            id=parent.id,
            paper_id=self.doc.paper_id,
            center=parent.id,
            concepts=members,
            links=_links(parent.id, links, key_to_id, self.build.warnings),
        )
        self.store.save_layer(layer)
        self.build.layers.append(layer)
        return layer

    # -- frontier (§10.5.2: whatever layer is on screen, two levels below exist) ----------

    async def ensure_frontier(
        self, layer_id: str, levels: int = 2, priority: Priority = "P1"
    ) -> list[Layer]:
        layer = self.store.get_layer(self.doc.paper_id, layer_id)
        if layer is None:
            return []
        made: list[Layer] = []
        current = list(layer.concepts)
        for _ in range(levels):
            new_layers = await self.skeletons(current, priority)
            made += new_layers
            nxt: list[str] = []
            for cid in current:
                child = self.store.get_layer(self.doc.paper_id, cid)
                if child is not None:
                    nxt += child.concepts
            current = list(dict.fromkeys(nxt))
        return made

    async def initial(self) -> ChartBuild:
        """Ingest-time work: master layer, then two levels of skeletons below it."""
        await self.master()
        await self.ensure_frontier(MASTER, levels=2)
        return self.build
