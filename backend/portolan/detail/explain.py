"""Explanations and edge explanations (spec §8 `detail.explain` / `detail.edge`, §10.7.1, §10.8)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from portolan.llm.gateway import Gateway
from portolan.llm.prompts import edge_explain
from portolan.llm.prompts import explain as explain_prompt
from portolan.llm.prompts.detail_schemas import EdgeOut, ExplainOut
from portolan.llm.types import Priority
from portolan.models.concept import MASTER, Concept, Link
from portolan.models.detail import Angle, EdgeExplanation, Explanation
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore

WORDS = (200, 400)


class DetailError(Exception):
    """Plain-language problem (unknown concept or link)."""


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", re.sub(r"\$[^$]*\$", " x ", text)))


def _passages(doc: PaperDoc, anchor_ids: list[str], limit: int = 4) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for aid in anchor_ids:
        a = doc.anchor(aid)
        if a is not None and a.text:
            out.append({"id": a.id, "label": a.label, "text": a.text[:1200]})
        if len(out) >= limit:
            break
    if not out and doc.abstract:
        out.append({"id": "abstract", "label": "Abstract", "text": doc.abstract[:1200]})
    return out


def path_titles(store: ChartStore, paper_id: str, path: list[str]) -> list[str]:
    concepts = store.concepts(paper_id, path)
    return [concepts[c].title for c in path if c in concepts]


def default_path(store: ChartStore, paper_id: str, concept: Concept) -> list[str]:
    """concept ids master center → … → concept, following the shallowest parent each step."""
    chain = [concept.id]
    seen = {concept.id}
    current = concept
    while current.depth > 0:
        parents = [
            store.get_concept(paper_id, lid)
            for lid in store.layers_containing(paper_id, current.id)
            if lid != current.id
        ]
        master = store.get_layer(paper_id, MASTER)
        if master is not None and current.id in master.concepts:
            parents.append(store.get_concept(paper_id, master.center))
        ranked = sorted(
            (p for p in parents if p is not None and p.id not in seen), key=lambda p: p.depth
        )
        if not ranked:
            break
        current = ranked[0]
        seen.add(current.id)
        chain.append(current.id)
    return list(reversed(chain))


def body_markdown(out: ExplainOut, doc: PaperDoc) -> str:
    s = out.sections
    labels = [doc.anchor(a).label for a in s.where_it_appears if doc.anchor(a)]  # type: ignore[union-attr]
    parts = [
        f"**In one sentence.** {out.gist}",
        f"**What it is.** {s.what_it_is}",
        f"**How it works.** {s.how_it_works}",
        f"**Why it's here.** {s.why_its_here}",
        f"**Where it appears.** {', '.join(labels) or 'Not tied to a specific passage.'}",
        f"**Common confusion.** {s.common_confusion}",
    ]
    return "\n\n".join(parts)


async def explain(
    doc: PaperDoc,
    gateway: Gateway,
    store: ChartStore,
    concept_id: str,
    angle: Angle = "default",
    path: list[str] | None = None,
    priority: Priority = "P0",
) -> tuple[Explanation, list[str]]:
    """Generate an explanation, with quality warnings (e.g. outside 200-400 words)."""
    concept = store.get_concept(doc.paper_id, concept_id)
    if concept is None:
        raise DetailError("That concept isn't on this paper's map.")
    path = path or default_path(store, doc.paper_id, concept)
    titles = path_titles(store, doc.paper_id, path)
    center = titles[-2] if len(titles) >= 2 else titles[-1] if titles else concept.title
    inputs: dict[str, Any] = {
        "paper_title": doc.title,
        "concept": {"title": concept.title, "gist": concept.gist},
        "path": titles or [concept.title],
        "layer_center": center,
        "passages": _passages(doc, concept.anchors),
        "symbols": doc.symbols,
    }
    result = await gateway.call(
        "default", explain_prompt.TEMPLATE, inputs, ExplainOut, priority, doc.paper_id
    )
    out: ExplainOut = result.parsed
    valid = {a.id for a in doc.anchors}
    out.sections.where_it_appears = [a for a in out.sections.where_it_appears if a in valid]
    md = body_markdown(out, doc)
    words = word_count(md)
    warnings: list[str] = []
    if not WORDS[0] <= words <= WORDS[1] + 60:  # headings add a few words
        warnings.append(f"explanation for {concept_id} has {words} words (target 200-400)")
    explanation = Explanation(
        concept_id=concept_id,
        angle=angle,
        path_context=path,
        gist=out.gist,
        sections=out.sections,
        body_markdown=md,
        anchors_cited=out.sections.where_it_appears,
        model=result.model,
        created_at=datetime.now(UTC),
        word_count=words,
    )
    return explanation, warnings


def find_link(store: ChartStore, paper_id: str, link_id: str) -> Link | None:
    layer_id = link_id.split(":", 1)[0]
    layer = store.get_layer(paper_id, layer_id)
    if layer is None:
        return None
    return next((link for link in layer.links if link.id == link_id), None)


async def explain_edge(
    doc: PaperDoc, gateway: Gateway, store: ChartStore, link_id: str, priority: Priority = "P0"
) -> EdgeExplanation:
    link = find_link(store, doc.paper_id, link_id)
    if link is None:
        raise DetailError("That link isn't on this paper's map.")
    concepts = store.concepts(doc.paper_id, [link.source, link.target])
    source, target = concepts.get(link.source), concepts.get(link.target)
    if source is None or target is None:
        raise DetailError("That link points at a concept that isn't on the map.")
    layer = store.get_layer(doc.paper_id, link.layer_id)
    center = store.get_concept(doc.paper_id, layer.center) if layer else None
    path_ids = default_path(store, doc.paper_id, center) if center else []
    inputs: dict[str, Any] = {
        "paper_title": doc.title,
        "source": {"title": source.title, "gist": source.gist},
        "target": {"title": target.title, "gist": target.gist},
        "label": link.label,
        "layer_center": center.title if center else doc.title,
        "path": path_titles(store, doc.paper_id, path_ids),
        "passages": _passages(doc, [*source.anchors, *target.anchors]),
    }
    result = await gateway.call(
        "default", edge_explain.TEMPLATE, inputs, EdgeOut, priority, doc.paper_id
    )
    out: EdgeOut = result.parsed
    return EdgeExplanation(
        link_id=link_id,
        headline=out.headline,
        body_markdown=out.body,
        why_it_matters=out.why_it_matters,
    )
