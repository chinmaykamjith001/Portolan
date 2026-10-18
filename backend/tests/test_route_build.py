"""Route generation: deterministic structure, LLM words validated, fallbacks, sub-routes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel

from portolan.detail.explain import DetailError
from portolan.ingest.anchors import DocBuilder
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from portolan.models.concept import MASTER, Concept, Layer
from portolan.models.paper import PaperDoc, PaperRef
from portolan.models.route import Route
from portolan.route.build import (
    Tree,
    Unit,
    build_main_route,
    build_sub_route,
    main_plan,
    resolve_unit,
    units_for,
)
from portolan.store.chart import ChartStore
from portolan.store.db import Database, open_database
from portolan.store.routes import RouteStore
from tests.detail_helpers import gateway as make_gateway

PAPER_ID = "arxiv:2101.00001v1"


def paper() -> PaperDoc:
    b = DocBuilder()
    b.section(1, "1", "Introduction")
    for i in range(1, 5):
        b.paragraph(f"Introduction paragraph {i}. Recurrent models are slow to train.")
    b.paragraph("Details of the proofs are in Appendix A.")
    b.section(1, "2", "Background")
    b.paragraph("Attention maps queries and keys to an output.")
    b.section(1, "3", "Model Architecture")
    b.section(2, "3.1", "Encoder")
    b.paragraph("The encoder stacks identical layers.")
    b.paragraph("Each layer has two sublayers.")
    b.section(2, "3.2", "Attention")
    b.paragraph("We compute scaled dot-product attention.")
    b.equation(r"\mathrm{softmax}(QK^T/\sqrt{d_k})V", "1")
    b.section(3, "3.2.1", "Scaling")
    b.paragraph("The scale keeps the softmax out of its flat region.")
    b.section(3, "3.2.2", "Masking")
    b.paragraph("Masking blocks future positions.")
    b.section(2, "3.3", "Positional Encoding")
    b.paragraph("Sinusoids inject order.")
    b.section(1, "4", "Training")
    b.paragraph("We train on WMT 2014 with Adam.")
    b.section(1, "5", "Empty Section")
    b.section(1, None, "References")
    b.paragraph("[1] Someone. A paper.")
    b.start_appendix()
    b.section(1, "A", "Proofs")
    b.paragraph("The proof is short.")
    b.equation(r"x = x", "9")
    return b.build(
        paper_id=PAPER_ID,
        ref=PaperRef(arxiv_id="2101.00001", version="v1"),
        title="A Toy Paper",
        authors=["A. Author"],
        abstract="We study toys.",
    )


class RouteLLM:
    """Writes valid words for every stop it is shown; `mutate` lets a test bend the reply."""

    name = "route-llm"
    uses_quota = False

    def __init__(self, mutate: Any = None, fail: bool = False) -> None:
        self.mutate = mutate
        self.fail = fail
        self.users: list[str] = []

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        user = messages[-1].content
        self.users.append(user)
        if self.fail:
            return ProviderResponse(text="not json", usage=Usage(), model="fake")
        keys = re.findall(r"^\[(s\d+)\] ", user, re.M)
        stops = [
            {
                "key": k,
                "title": f"Title {k}",
                "role": "definition",
                "summary": f"Summary {k}.",
                "concept_id": None,
                "transition_to_next": f"then comes {k},",
            }
            for k in keys
        ]
        body: dict[str, Any] = {"stops": stops}
        if self.mutate:
            self.mutate(body)
        return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")


def gw(db: Database, llm: RouteLLM) -> Gateway:
    return make_gateway(db, cast(Any, llm))


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "t.db")


def concept(cid: str, title: str, anchors: list[str], *, has_layer: bool = False) -> Concept:
    return Concept(
        id=cid, canonical_name=title.lower(), title=title, gist=f"{title}.", depth=0,
        anchors=anchors, has_layer=has_layer,
    )  # fmt: skip


def chart_with_concepts(db: Database) -> ChartStore:
    chart = ChartStore(db)
    chart.upsert_concept(PAPER_ID, concept("model", "Toy Model", ["sec:1"], has_layer=True))
    chart.upsert_concept(PAPER_ID, concept("attn", "Attention", ["sec:3.2"], has_layer=True))
    chart.upsert_concept(PAPER_ID, concept("scale", "Scaling", ["sec:3.2.1"]))
    chart.upsert_concept(PAPER_ID, concept("elsewhere", "Elsewhere", ["sec:4"]))
    chart.save_layer(
        Layer(id=MASTER, paper_id=PAPER_ID, center="model", concepts=["attn"], links=[])
    )
    chart.save_layer(
        Layer(
            id="attn", paper_id=PAPER_ID, center="attn", concepts=["scale", "elsewhere"], links=[]
        )
    )
    return chart


async def build(
    db: Database, llm: RouteLLM, *, chart: ChartStore | None = None, doc: PaperDoc | None = None
) -> Route:
    doc = doc or paper()
    return await build_main_route(
        doc,
        gw(db, llm),
        chart or ChartStore(db),
        RouteStore(db),
    )


# -- structure -----------------------------------------------------------------------------------


async def test_stops_follow_the_papers_order(db: Database) -> None:
    doc = paper()
    route = await build(db, RouteLLM(), doc=doc)
    order = {a.id: a.order for a in doc.anchors}
    positions = [order[s.anchor_id] for s in route.stops]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert [s.id for s in route.stops] == [f"stop:{n}" for n in range(1, len(route.stops) + 1)]
    assert [s.order for s in route.stops] == list(range(1, len(route.stops) + 1))
    assert {s.route_id for s in route.stops} == {"main"} and route.id == "main"
    assert route.parent_stop_id is None and route.paper_id == PAPER_ID


async def test_sections_yield_one_to_five_stops_and_skip_back_matter(db: Database) -> None:
    doc = paper()
    route = await build(db, RouteLLM(), doc=doc)
    by_top: dict[str, int] = {}
    for s in route.stops:
        a = next(x for x in doc.anchors if x.id == s.anchor_id)
        while a.parent_id is not None:
            a = next(x for x in doc.anchors if x.id == a.parent_id)
        by_top[a.id] = by_top.get(a.id, 0) + 1
    assert set(by_top) == {
        "sec:1",
        "sec:2",
        "sec:3",
        "sec:4",
        "sec:5",
    }  # no References, no Appendix
    assert all(1 <= n <= 5 for n in by_top.values())
    assert by_top["sec:1"] == 2  # five paragraphs, three to a stop
    assert not any(s.anchor_id.startswith("sec:A") for s in route.stops)


async def test_segment_titles_mark_the_first_stop_of_each_section(db: Database) -> None:
    route = await build(db, RouteLLM())
    marked = [s for s in route.stops if s.segment_title]
    assert [s.segment_title for s in marked] == [
        "§1 Introduction",
        "§2 Background",
        "§3 Model Architecture",
        "§4 Training",
        "§5 Empty Section",
    ]
    firsts = [
        next(s for s in route.stops if s.anchor_id.split(":")[1].split(".")[0] == n)
        for n in "12345"
    ]
    assert [s.id for s in marked] == [s.id for s in firsts]


async def test_section_labels_come_from_anchors(db: Database) -> None:
    route = await build(db, RouteLLM())
    by_anchor = {s.anchor_id: s for s in route.stops}
    assert by_anchor["sec:3.1"].section_label == "§3.1"
    assert by_anchor["para:1:1"].section_label == "§1 ¶1"


async def test_appendix_is_a_side_road_on_the_stop_that_cites_it(db: Database) -> None:
    route = await build(db, RouteLLM())
    with_road = [s for s in route.stops if s.side_roads]
    assert len(with_road) == 1 and with_road[0].side_roads == ["sec:A"]
    assert with_road[0].anchor_id == "para:1:4"  # the group holding "Appendix A" (¶5 of §1)


async def test_empty_section_still_has_a_stop_without_calling_the_llm(db: Database) -> None:
    llm = RouteLLM()
    route = await build(db, llm)
    stop = next(s for s in route.stops if s.anchor_id == "sec:5")
    assert stop.title == "Empty Section" and stop.summary == "" and stop.role == "other"
    assert len(llm.users) == 4  # sections 1-4; section 5 has no text


# -- the LLM's words -----------------------------------------------------------------------------


async def test_words_are_validated_and_clipped(db: Database) -> None:
    def mutate(body: dict[str, Any]) -> None:
        first = body["stops"][0]
        first["summary"] = " ".join(f"w{i}" for i in range(80))
        first["role"] = "banana"
        first["transition_to_next"] = "and so on and so forth and on and on and on and on and on"
        body["stops"][-1]["transition_to_next"] = "so the authors move on"

    route = await build(db, RouteLLM(mutate))
    s = route.stops[0]
    assert len(s.summary.split()) == 40 and s.role == "other"
    assert s.transition_to_next and s.transition_to_next.endswith(",")
    assert len(s.transition_to_next.split()) <= 12
    assert route.stops[-1].transition_to_next is None  # the road ends


async def test_invalid_concept_ids_are_dropped(db: Database) -> None:
    chart = chart_with_concepts(db)

    def mutate(body: dict[str, Any]) -> None:
        body["stops"][0]["concept_id"] = "model"
        if len(body["stops"]) > 1:
            body["stops"][1]["concept_id"] = "ghost"

    route = await build(db, RouteLLM(mutate), chart=chart)
    assert route.stops[0].concept_id == "model"
    assert all(s.concept_id in (None, "model") for s in route.stops)


async def test_candidates_are_master_concepts_plus_local_level_one(db: Database) -> None:
    chart = chart_with_concepts(db)
    llm = RouteLLM()
    await build(db, llm, chart=chart)
    section_3 = next(u for u in llm.users if "Section: §3 Model Architecture" in u)
    candidates = section_3.split("Candidate concepts")[1].split("<untrusted")[0]
    assert "attn:" in candidates and "scale:" in candidates  # 'scale' sits inside §3.2.1
    assert "elsewhere:" not in candidates  # anchored in §4, not this section


async def test_failed_call_falls_back_to_headings(db: Database) -> None:
    route = await build(db, RouteLLM(fail=True))
    assert len(route.stops) >= 5
    stop = next(s for s in route.stops if s.anchor_id == "sec:3.1")
    assert stop.title == "Encoder" and stop.summary == "" and stop.transition_to_next is None
    assert all(s.concept_id is None for s in route.stops)


async def test_a_missing_stop_in_the_reply_falls_back_alone(db: Database) -> None:
    def mutate(body: dict[str, Any]) -> None:
        if len(body["stops"]) > 1:
            body["stops"].pop(0)

    route = await build(db, RouteLLM(mutate))
    sec3 = [s for s in route.stops if s.anchor_id.startswith("sec:3")]
    assert sec3[0].title == "Encoder"  # fell back
    assert all(s.title.startswith("Title ") for s in sec3[1:])


async def test_recurring_concepts_point_both_ways(db: Database) -> None:
    chart = chart_with_concepts(db)

    def mutate(body: dict[str, Any]) -> None:
        for s in body["stops"]:
            s["concept_id"] = "attn"

    route = await build(db, RouteLLM(mutate), chart=chart)
    seen = [s for s in route.stops if s.concept_id == "attn"]
    assert len(seen) >= 3
    head, rest = seen[0], seen[1:]
    assert head.first_stop_id is None and head.seen_again_at == [s.id for s in rest]
    assert all(s.first_stop_id == head.id and s.seen_again_at == [] for s in rest)


async def test_building_twice_gives_identical_ids(db: Database) -> None:
    a = await build(db, RouteLLM())
    b = await build(db, RouteLLM())
    assert [(s.id, s.anchor_id) for s in a.stops] == [(s.id, s.anchor_id) for s in b.stops]


# -- planning ------------------------------------------------------------------------------------


def test_too_many_units_merge_evenly() -> None:
    tree = Tree(paper())
    sec = next(a for a in tree.anchors if a.id == "sec:1")
    paragraphs = tree.children[sec.id] * 4  # 20 paragraph anchors, one per unit when per_stop=1
    units = units_for(paragraphs, per_stop=1, limit=5)
    assert len(units) == 5 and [len(u.anchors) for u in units] == [4, 4, 4, 4, 4]
    assert all(isinstance(u, Unit) for u in units)


async def test_stops_with_substructure_have_sub_routes(db: Database) -> None:
    route = await build(db, RouteLLM())
    openable = {s.anchor_id: s.sub_route_id for s in route.stops if s.sub_route_id}
    assert openable["sec:3.1"] == next(s.id for s in route.stops if s.anchor_id == "sec:3.1")
    assert "sec:3.2" in openable  # has subsections
    assert "sec:3.3" not in openable  # a single paragraph: nothing to open
    assert "para:1:1" in openable  # three paragraphs share this stop


# -- sub-routes ----------------------------------------------------------------------------------


async def test_sub_route_ids_and_order(db: Database) -> None:
    doc = paper()
    routes = RouteStore(db)
    llm = RouteLLM()
    gateway = gw(db, llm)
    chart = ChartStore(db)
    main = await build_main_route(doc, gateway, chart, routes)
    routes.save(main)
    stop = next(s for s in main.stops if s.anchor_id == "sec:3.2")
    sub = await build_sub_route(doc, gateway, chart, routes, stop.id)
    assert sub.id == stop.id and sub.parent_stop_id == stop.id and sub.paper_id == PAPER_ID
    assert [s.id for s in sub.stops] == [f"{stop.id}.{k}" for k in (1, 2, 3)]
    assert [s.anchor_id for s in sub.stops] == ["para:3.2:1", "sec:3.2.1", "sec:3.2.2"]
    assert {s.route_id for s in sub.stops} == {stop.id}
    assert sub.stops[-1].transition_to_next is None
    # the equation belongs to the paragraph's text
    assert any("softmax" in u for u in llm.users[-1:])
    # a sub-route stop that has structure of its own can be opened in turn
    nested = next(s for s in sub.stops if s.anchor_id == "sec:3.2.1")
    assert nested.sub_route_id is None  # a single paragraph


async def test_sub_route_of_a_paragraph_group(db: Database) -> None:
    doc = paper()
    routes = RouteStore(db)
    gateway = gw(db, RouteLLM())
    chart = ChartStore(db)
    main = await build_main_route(doc, gateway, chart, routes)
    routes.save(main)
    group = next(s for s in main.stops if s.anchor_id == "para:1:1")
    sub = await build_sub_route(doc, gateway, chart, routes, group.id)
    assert [s.anchor_id for s in sub.stops] == ["para:1:1", "para:1:2", "para:1:3"]
    assert [s.section_label for s in sub.stops] == ["§1 ¶1", "§1 ¶2", "§1 ¶3"]


async def test_sub_route_errors_are_plain(db: Database) -> None:
    doc = paper()
    routes = RouteStore(db)
    gateway = gw(db, RouteLLM())
    chart = ChartStore(db)
    with pytest.raises(DetailError):
        await build_sub_route(doc, gateway, chart, routes, "stop:1")  # main route not saved yet
    main = await build_main_route(doc, gateway, chart, routes)
    routes.save(main)
    plain = next(s for s in main.stops if s.anchor_id == "sec:3.3")
    with pytest.raises(DetailError):
        await build_sub_route(doc, gateway, chart, routes, plain.id)
    with pytest.raises(DetailError):
        await build_sub_route(doc, gateway, chart, routes, "stop:999")


def test_resolve_unit_matches_the_plan() -> None:
    tree = Tree(paper())
    plan = main_plan(tree)
    assert all(resolve_unit(tree, u.key) is not None for u in plan)
    assert resolve_unit(tree, "stop:0") is None and resolve_unit(tree, "nope") is None
    assert resolve_unit(tree, "stop:x.y") is None
