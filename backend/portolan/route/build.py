"""Route generation (spec §10.6).

The shape of the route is deterministic: stops come from the paper's section tree, in the paper's
own order. The LLM only writes words for stops that already exist (title, role, summary, concept,
transition), one `fast` call per top-level section, so a failed call degrades to heading titles
instead of breaking the route.

Stop ids are stable because the plan is deterministic: `stop:<n>` in document order for the main
route, `<stop_id>.<k>` for the k-th stop of a stop's sub-route. A sub-route is rebuilt by
re-planning the main route and descending to the stop, so no extra state is stored.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, get_args

from pydantic import ValidationError

from portolan.detail.explain import DetailError
from portolan.llm.gateway import Gateway
from portolan.llm.prompts import route_section
from portolan.llm.prompts.route_schemas import RouteSectionOut
from portolan.llm.types import LLMError, Priority
from portolan.models.concept import MASTER, Concept
from portolan.models.paper import Anchor, PaperDoc
from portolan.models.route import Route, Stop, StopRole
from portolan.store.chart import ChartStore
from portolan.store.routes import RouteStore

MAX_STOPS_PER_SECTION = 5  # spec §10.6.1: each top-level section yields 1-5 stops
MAX_STOPS_PER_SUBROUTE = 12
PARAGRAPHS_PER_STOP = 3  # main route: consecutive paragraphs share a stop
SUMMARY_WORDS = 40
TRANSITION_WORDS = 12
TITLE_WORDS = 8
SECTION_TEXT_BUDGET = 12_000  # characters of paper text sent per call
ROLES: tuple[str, ...] = get_args(StopRole)
SKIPPED_SECTIONS = re.compile(r"^(references|bibliography|acknowledg(e)?ments?)$", re.IGNORECASE)


# -- the paper's section tree --------------------------------------------------------------------


class Tree:
    """Anchors by parent, in document order."""

    def __init__(self, doc: PaperDoc) -> None:
        self.doc = doc
        self.anchors = sorted(doc.anchors, key=lambda a: a.order)
        self.children: dict[str | None, list[Anchor]] = defaultdict(list)
        for a in self.anchors:
            self.children[a.parent_id].append(a)

    def descendants(self, anchor: Anchor) -> list[Anchor]:
        out: list[Anchor] = []
        stack = list(reversed(self.children.get(anchor.id, [])))
        while stack:
            a = stack.pop()
            out.append(a)
            stack.extend(reversed(self.children.get(a.id, [])))
        return out


@dataclass
class Unit:
    """What one stop covers: its anchors in order; the first one is the stop's anchor."""

    anchors: list[Anchor]
    section: Anchor | None = None  # the top-level section it belongs to (main route only)
    key: str = ""
    sub_count: int = field(default=0, repr=False)


def _group_evenly(units: list[Unit], limit: int) -> list[Unit]:
    """Merge consecutive units so at most `limit` remain, as evenly as possible."""
    if len(units) <= limit:
        return units
    size, extra = divmod(len(units), limit)
    out: list[Unit] = []
    i = 0
    for k in range(limit):
        n = size + (1 if k < extra else 0)
        chunk = units[i : i + n]
        out.append(Unit([a for u in chunk for a in u.anchors], chunk[0].section))
        i += n
    return out


def units_for(content: list[Anchor], *, per_stop: int, limit: int) -> list[Unit]:
    """Split a container's content into stops: each subsection is a stop; consecutive paragraphs
    form groups of `per_stop`; equations, tables and figures stay with the paragraph before them."""
    units: list[Unit] = []
    open_group: Unit | None = None
    paragraphs = 0
    for a in content:
        if a.kind == "section":
            open_group = None
            units.append(Unit([a]))
        elif a.kind == "paragraph":
            if open_group is None or paragraphs >= per_stop:
                open_group = Unit([a])
                units.append(open_group)
                paragraphs = 1
            else:
                open_group.anchors.append(a)
                paragraphs += 1
        elif open_group is not None:
            open_group.anchors.append(a)
        else:
            open_group = Unit([a])
            units.append(open_group)
            paragraphs = 0
    return _group_evenly(units, limit)


def content_of(tree: Tree, unit: Unit) -> list[Anchor]:
    """The anchors a unit opens into: a section's children, or the unit's own blocks."""
    out: list[Anchor] = []
    for a in unit.anchors:
        out.extend(tree.children.get(a.id, []) if a.kind == "section" else [a])
    return out


def sub_units(tree: Tree, unit: Unit) -> list[Unit]:
    """The stops of a unit's sub-route; at most one stop means there is nothing to open."""
    subs = units_for(content_of(tree, unit), per_stop=1, limit=MAX_STOPS_PER_SUBROUTE)
    if len(subs) >= 2 or (len(subs) == 1 and subs[0].anchors[0].kind == "section"):
        return subs
    return []


def top_level_sections(tree: Tree) -> list[Anchor]:
    return [
        a
        for a in tree.children.get(None, [])
        if a.kind == "section" and not a.is_appendix and not SKIPPED_SECTIONS.match(a.text.strip())
    ]


def main_plan(tree: Tree) -> list[Unit]:
    """Every main-route stop in order, with `stop:<n>` keys."""
    plan: list[Unit] = []
    for section in top_level_sections(tree):
        units = units_for(
            tree.children.get(section.id, []),
            per_stop=PARAGRAPHS_PER_STOP,
            limit=MAX_STOPS_PER_SECTION,
        )
        if not units:  # a heading with nothing under it still gets a stop
            units = [Unit([section])]
        for u in units:
            u.section = section
        plan.extend(units)
    for n, u in enumerate(plan, 1):
        u.key = f"stop:{n}"
    return plan


def resolve_unit(tree: Tree, stop_id: str) -> Unit | None:
    """Re-derive the unit behind a stop id (`stop:3`, `stop:3.2`, `stop:3.2.1`, ...)."""
    if not stop_id.startswith("stop:"):
        return None
    try:
        parts = [int(p) for p in stop_id.removeprefix("stop:").split(".")]
    except ValueError:
        return None
    plan = main_plan(tree)
    if not 1 <= parts[0] <= len(plan):
        return None
    unit = plan[parts[0] - 1]
    for k in parts[1:]:
        subs = sub_units(tree, unit)
        if not 1 <= k <= len(subs):
            return None
        unit = subs[k - 1]
    return unit


# -- text for prompts and fallbacks --------------------------------------------------------------


def _words(text: str, n: int) -> str:
    parts = text.split()
    return " ".join(parts[:n])


def unit_text(tree: Tree, unit: Unit, limit: int) -> str:
    chunks: list[str] = []
    for a in unit.anchors:
        if a.kind == "section":
            chunks.append(a.text)
            chunks.extend(_block_text(d) for d in tree.descendants(a) if d.kind != "section")
        else:  # a paragraph carries its equations (they hang under it in the anchor tree)
            chunks.append(_block_text(a))
            chunks.extend(_block_text(d) for d in tree.descendants(a) if d.kind != "section")
    text = "\n".join(c for c in chunks if c)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _block_text(a: Anchor) -> str:
    if a.kind == "equation":
        return f"({a.label}) {a.text}"
    return a.text


def has_body(tree: Tree, unit: Unit) -> bool:
    """False for a bare heading: there is nothing to paraphrase, so no LLM call."""
    for a in unit.anchors:
        for b in [a, *tree.descendants(a)]:
            if b.kind != "section" and b.text.strip():
                return True
    return False


def fallback_title(unit: Unit) -> str:
    first = unit.anchors[0]
    if first.kind == "section":
        return _words(first.text, 12) or first.label
    return _words(first.text, TITLE_WORDS).rstrip(",;:") + "…" if first.text else first.label


def section_label(unit: Unit) -> str:
    return unit.anchors[0].label


# -- concepts ------------------------------------------------------------------------------------


def candidate_concepts(chart: ChartStore, paper_id: str, anchor_ids: set[str]) -> list[Concept]:
    """Master-layer concepts, plus level-1 concepts whose anchors fall inside this section."""
    master = chart.get_layer(paper_id, MASTER)
    if master is None:
        return []
    out: dict[str, Concept] = {}
    ids = [master.center, *master.concepts]
    concepts = chart.concepts(paper_id, ids)
    for cid in ids:
        if cid in concepts:
            out[cid] = concepts[cid]
    for cid in master.concepts:
        c = concepts.get(cid)
        if c is None or not c.has_layer:
            continue
        layer = chart.get_layer(paper_id, cid)
        if layer is None:
            continue
        for child in chart.concepts(paper_id, layer.concepts).values():
            if child.id not in out and anchor_ids.intersection(child.anchors):
                out[child.id] = child
    return list(out.values())


# -- the LLM step --------------------------------------------------------------------------------


@dataclass
class Words:
    title: str
    role: StopRole
    summary: str
    concept_id: str | None
    transition: str | None


def _coerce_role(value: str) -> StopRole:
    v = value.strip().lower()
    return v if v in ROLES else "other"  # type: ignore[return-value]


def _clean_transition(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    t = _words(text.strip(), TRANSITION_WORDS)
    return t if t.endswith(",") else t.rstrip(".;:") + ","


def _validate(
    out: RouteSectionOut, keyed: dict[str, Unit], valid_concepts: set[str]
) -> dict[str, Words]:
    """Words per stop id; a stop the reply skipped (or left untitled) falls back on its own."""
    by_key = {s.key: s for s in out.stops}
    words: dict[str, Words] = {}
    for skey, u in keyed.items():
        s = by_key.get(skey)
        if s is None or not s.title.strip():
            continue
        words[u.key] = Words(
            title=_words(s.title.strip(), TITLE_WORDS * 2),
            role=_coerce_role(s.role),
            summary=_words(s.summary.strip(), SUMMARY_WORDS),
            concept_id=s.concept_id if s.concept_id in valid_concepts else None,
            transition=_clean_transition(s.transition_to_next),
        )
    return words


async def write_words(
    doc: PaperDoc,
    gateway: Gateway,
    tree: Tree,
    units: list[Unit],
    *,
    section: dict[str, str],
    next_section: dict[str, str] | None,
    concepts: list[Concept],
    priority: Priority,
) -> dict[str, Words]:
    """One call for a group of stops; `{}` (every stop falls back) with no text or on failure.

    The prompt uses short keys (s1, s2, ...) so the model never has to echo a stop id."""
    per_unit = max(400, SECTION_TEXT_BUDGET // max(1, len(units)))
    stops: list[dict[str, str]] = []
    keyed: dict[str, Unit] = {}
    for u in units:
        if not has_body(tree, u):
            continue
        text = unit_text(tree, u, per_unit)
        skey = f"s{len(stops) + 1}"
        keyed[skey] = u
        stops.append(
            {
                "key": skey,
                "label": section_label(u),
                "kind": "section" if u.anchors[0].kind == "section" else "passage",
                "title": fallback_title(u),
                "text": text,
            }
        )
    if not stops:
        return {}
    inputs: dict[str, Any] = {
        "paper_title": doc.title,
        "section": section,
        "next_section": next_section,
        "stops": stops,
        "concepts": [{"id": c.id, "title": c.title, "gist": c.gist} for c in concepts],
    }
    try:
        result = await gateway.call(
            "fast", route_section.TEMPLATE, inputs, RouteSectionOut, priority, doc.paper_id
        )
    except (LLMError, ValidationError, ValueError):
        return {}
    out: RouteSectionOut = result.parsed
    return _validate(out, keyed, {c.id for c in concepts})


# -- assembling stops ----------------------------------------------------------------------------


def _make_stop(route_id: str, order: int, stop_id: str, unit: Unit, w: Words | None) -> Stop:
    return Stop(
        id=stop_id,
        route_id=route_id,
        order=order,
        title=w.title if w else fallback_title(unit),
        section_label=section_label(unit),
        anchor_id=unit.anchors[0].id,
        role=w.role if w else "other",
        concept_id=w.concept_id if w else None,
        summary=w.summary if w else "",
        transition_to_next=w.transition if w else None,
    )


def mark_recurring(stops: list[Stop]) -> None:
    """§10.6.4: later mentions point back; the first mention lists where it is seen again."""
    first: dict[str, Stop] = {}
    for stop in stops:
        if stop.concept_id is None:
            continue
        head = first.get(stop.concept_id)
        if head is None:
            first[stop.concept_id] = stop
        else:
            stop.first_stop_id = head.id
            head.seen_again_at.append(stop.id)


def _appendix_pattern(a: Anchor) -> re.Pattern[str] | None:
    number = a.id.removeprefix("sec:")
    if not re.fullmatch(r"[A-Za-z0-9.]+", number):
        return None
    return re.compile(
        rf"\b(?:appendix|appendices|section|sec\.?|§)\s*~?{re.escape(number)}\b", re.I
    )


def attach_side_roads(tree: Tree, plan: list[Unit], stops: list[Stop]) -> None:
    """Appendices are side roads off the first stop that refers to them, never main stops."""
    appendix_sections = [a for a in tree.anchors if a.kind == "section" and a.is_appendix]
    if not appendix_sections:
        return
    top_of: dict[str, str] = {}  # appendix anchor id -> its top-level appendix section id
    for a in appendix_sections:
        cur = a
        while cur.parent_id is not None and cur.parent_id in {s.id for s in appendix_sections}:
            cur = next(s for s in appendix_sections if s.id == cur.parent_id)
        top_of[a.id] = cur.id
    patterns = [(a, _appendix_pattern(a)) for a in appendix_sections]
    equation_home: dict[str, str] = {}
    for a in tree.anchors:
        if a.kind == "equation" and a.is_appendix:
            for parent in appendix_sections:
                if a.parent_id == parent.id:
                    equation_home[a.id] = top_of[parent.id]
    claimed: set[str] = set()
    for unit, stop in zip(plan, stops, strict=True):
        text = unit_text(tree, unit, 10**9)
        found: list[str] = []
        for a, pattern in patterns:
            if pattern is not None and pattern.search(text):
                found.append(top_of[a.id])
        for anchor in (x for u_a in unit.anchors for x in [u_a, *tree.descendants(u_a)]):
            for eq_id, refs in tree.doc.equation_refs.items():
                if anchor.id in refs and eq_id in equation_home:
                    found.append(equation_home[eq_id])
        for sid in dict.fromkeys(found):
            if sid not in claimed:
                claimed.add(sid)
                stop.side_roads.append(sid)


def _section_ref(a: Anchor) -> dict[str, str]:
    return {"label": a.label, "title": a.text}


# -- public API ----------------------------------------------------------------------------------


async def build_main_route(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, routes: RouteStore
) -> Route:
    """Stops come deterministically from PaperDoc; one `fast` call per top-level section writes
    titles, roles, summaries, concept links and transitions. The caller saves the result."""
    tree = Tree(doc)
    plan = main_plan(tree)
    sections = top_level_sections(tree)
    stops: list[Stop] = []
    for si, section in enumerate(sections):
        units = [u for u in plan if u.section is section]
        nxt = sections[si + 1] if si + 1 < len(sections) else None
        inside = {d.id for d in tree.descendants(section)} | {section.id}
        words = await write_words(
            doc,
            gateway,
            tree,
            units,
            section=_section_ref(section),
            next_section=_section_ref(nxt) if nxt else None,
            concepts=candidate_concepts(chart, doc.paper_id, inside),
            priority="P1",
        )
        for k, u in enumerate(units):
            stop = _make_stop("main", len(stops) + 1, u.key, u, words.get(u.key))
            if k == 0:
                stop.segment_title = (
                    section.text
                    if section.label == section.text
                    else f"{section.label} {section.text}"
                )
            if sub_units(tree, u):
                stop.sub_route_id = u.key
            stops.append(stop)
    if stops:
        stops[-1].transition_to_next = None
    mark_recurring(stops)
    attach_side_roads(tree, plan, stops)
    return Route(id="main", paper_id=doc.paper_id, parent_stop_id=None, stops=stops)


async def build_sub_route(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, routes: RouteStore, stop_id: str
) -> Route:
    """Sub-route for one stop, from that section's paragraphs in order (id == stop_id)."""
    tree = Tree(doc)
    unit = resolve_unit(tree, stop_id)
    parent = routes.find_stop_route(doc.paper_id, stop_id)
    parent_stop = next((s for s in parent.stops if s.id == stop_id), None) if parent else None
    if unit is None or parent_stop is None:
        raise DetailError("That stop isn't on this paper's route.")
    subs = sub_units(tree, unit)
    if not subs:
        raise DetailError("There is nothing further to open under this stop.")
    for k, u in enumerate(subs, 1):
        u.key = f"{stop_id}.{k}"
    inside = {d.id for a in unit.anchors for d in [a, *tree.descendants(a)]}
    words = await write_words(
        doc,
        gateway,
        tree,
        subs,
        section={"label": parent_stop.section_label, "title": parent_stop.title},
        next_section=None,
        concepts=candidate_concepts(chart, doc.paper_id, inside),
        priority="P0",
    )
    stops: list[Stop] = []
    for k, u in enumerate(subs, 1):
        stop = _make_stop(stop_id, k, u.key, u, words.get(u.key))
        if sub_units(tree, u):
            stop.sub_route_id = u.key
        stops.append(stop)
    stops[-1].transition_to_next = None
    mark_recurring(stops)
    return Route(id=stop_id, paper_id=doc.paper_id, parent_stop_id=stop_id, stops=stops)
