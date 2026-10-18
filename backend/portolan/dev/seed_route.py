"""Demo data for the Route (no network, no LLM): a nine-stop main route over the toy paper.

Nine stops so the S layout shows all three columns. Stop 6 revisits self-attention (a recurring
concept), stop 8 has an appendix side road, and stops 2 and 3 open into sub-routes. Everything
is validated against the demo paper's anchors and the seeded concepts before it is written.
"""

from __future__ import annotations

from portolan.models.paper import Anchor, PaperDoc
from portolan.models.route import Route, Stop, StopRole
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.papers import load_paper, save_paper
from portolan.store.routes import RouteStore

APPENDIX_ID = "sec:A"
_APPENDIX_TEXT = "Attention visualizations"


def _stop(
    route_id: str,
    order: int,
    stop_id: str,
    anchor: str,
    label: str,
    title: str,
    role: StopRole,
    concept: str | None,
    summary: str,
    transition: str | None,
    **extra: object,
) -> Stop:
    return Stop.model_validate(
        {
            "id": stop_id, "route_id": route_id, "order": order, "title": title,
            "section_label": label, "anchor_id": anchor, "role": role, "concept_id": concept,
            "summary": summary, "transition_to_next": transition, **extra,
        }
    )  # fmt: skip


def main_route(paper_id: str) -> Route:
    s = _stop
    stops = [
        s("main", 1, "stop:1", "sec:1", "§1", "A transformer small enough to read", "first_mention",
          "toyformer", "The paper introduces a two-layer transformer small enough to read in one "
          "sitting, with every part written out.", "so the model is laid out in three parts,",
          segment_title="§1 Introduction"),
        s("main", 2, "stop:2", "sec:2", "§2", "Three working parts", "setting", "feed-forward",
          "The model mixes tokens with attention, adds order with positional encoding and "
          "transforms each token with a small network.",
          "starting with the piece that mixes tokens,",
          segment_title="§2 Model", sub_route_id="stop:2"),
        s("main", 3, "stop:3", "sec:2.1", "§2.1", "Attention mixes tokens", "definition",
          "self-attention", "Each token builds its new representation as a weighted mix of every "
          "token, using queries, keys and values.", "which comes down to one formula,",
          sub_route_id="stop:3", seen_again_at=["stop:6"]),
        s("main", 4, "stop:4", "eq:1", "Eq. 1", "Scores, scaled and normalized", "definition",
          "scaled-dot-product", "Queries are scored against keys, divided by the square root of "
          "the key size, then normalized.", "where the normalizing step is a softmax,"),
        s("main", 5, "stop:5", "eq:2", "Eq. 2", "Softmax over the scores", "definition", "softmax",
          "Softmax turns each row of scores into positive weights that sum to one.",
          "but attention alone cannot see word order,"),
        s("main", 6, "stop:6", "sec:2.2", "§2.2", "Attention cannot see order", "argument",
          "self-attention", "Because attention treats its input as a set, the model needs some "
          "other way to know where each token sits.", "so a position signal is added,",
          first_stop_id="stop:3"),
        s("main", 7, "stop:7", "eq:3", "Eq. 3", "Sines at many wavelengths", "definition",
          "sinusoids", "Each dimension of the position signal is a sine or cosine of the position "
          "at its own wavelength.", "with the architecture settled, the paper turns to training,"),
        s("main", 8, "stop:8", "sec:3", "§3", "How the toy model is trained", "setting",
          "label-smoothing", "Training softens the target labels slightly and follows a fixed "
          "learning-rate schedule.", "and the schedule has a warmup phase,",
          segment_title="§3 Training", side_roads=[APPENDIX_ID]),
        s("main", 9, "stop:9", "eq:4", "Eq. 4", "Warmup, then decay", "result", "warmup",
          "The learning rate rises linearly for the first steps, then decays with the inverse "
          "square root of the step.", None),
    ]  # fmt: skip
    return Route(id="main", paper_id=paper_id, parent_stop_id=None, stops=stops)


def sub_routes(paper_id: str) -> list[Route]:
    s = _stop
    model = Route(
        id="stop:2", paper_id=paper_id, parent_stop_id="stop:2",
        stops=[
            s("stop:2", 1, "stop:2.1", "sec:2.1", "§2.1", "Attention", "definition",
              "self-attention", "Every token looks at every token and takes a weighted mix.",
              "then attention gets a sense of order,"),
            s("stop:2", 2, "stop:2.2", "sec:2.2", "§2.2", "Positional encoding", "definition",
              "positional-encoding", "A fixed signal of sines and cosines is added to each "
              "token so that order is visible.", None),
        ],
    )  # fmt: skip
    attention = Route(
        id="stop:3", paper_id=paper_id, parent_stop_id="stop:3",
        stops=[
            s("stop:3", 1, "stop:3.1", "eq:1", "Eq. 1", "The attention formula", "definition",
              "scaled-dot-product", "Scores come from queries and keys, are scaled, normalized "
              "and used to weight the values.", "where the normalizer is a softmax,"),
            s("stop:3", 2, "stop:3.2", "eq:2", "Eq. 2", "The softmax", "definition", "softmax",
              "Exponentiate each score and divide by the total.", None),
        ],
    )  # fmt: skip
    return [model, attention]


def ensure_appendix(db: Database, doc: PaperDoc) -> PaperDoc:
    """The demo paper has no appendix; add one so the side road has somewhere to go."""
    if doc.anchor(APPENDIX_ID) is not None:
        return doc
    doc.anchors.append(
        Anchor(
            id=APPENDIX_ID, kind="section", label="Appendix A", text=_APPENDIX_TEXT,
            parent_id=None, order=len(doc.anchors), level=1, is_appendix=True,
        )
    )  # fmt: skip
    save_paper(db, doc)
    return doc


def validate(doc: PaperDoc, chart: ChartStore, routes: list[Route]) -> None:
    for route in routes:
        for stop in route.stops:
            for anchor_id in [stop.anchor_id, *stop.side_roads]:
                if doc.anchor(anchor_id) is None:
                    raise ValueError(f"stop {stop.id} points at a missing anchor {anchor_id}")
            if stop.concept_id and chart.get_concept(doc.paper_id, stop.concept_id) is None:
                raise ValueError(f"stop {stop.id} points at a missing concept {stop.concept_id}")


def seed_route(db: Database, paper_id: str) -> None:
    """Write the demo paper's main route and its two sub-routes."""
    doc = load_paper(db, paper_id)
    if doc is None:
        return
    doc = ensure_appendix(db, doc)
    routes = [main_route(paper_id), *sub_routes(paper_id)]
    validate(doc, ChartStore(db), routes)
    store = RouteStore(db)
    for route in routes:
        store.save(route)
