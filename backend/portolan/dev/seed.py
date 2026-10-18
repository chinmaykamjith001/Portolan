"""A small, fixed demo paper written straight into the database: no network, no LLM.

Used by the frontend end-to-end tests (Playwright) and handy for UI work. Every concept without
a child layer is a first principle, so opening a layer never queues LLM work; every concept has
a default explanation and every link an edge explanation, so the drawer and the edge popover
always have content. The content is hand-written for the toy transformer in the test fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from portolan.llm.prompts.detail_schemas import ExplainSections
from portolan.models.concept import MASTER, Concept, Grounding, Layer, Link, LinkKind
from portolan.models.detail import EdgeExplanation, Explanation
from portolan.models.paper import Anchor, AnchorKind, PaperDoc, PaperRef
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.details import DetailStore
from portolan.store.papers import save_paper

DEMO_PAPER_ID = "demo:toyformer"
_CREATED = datetime(2026, 10, 17, tzinfo=UTC)


@dataclass(frozen=True)
class _C:
    id: str
    title: str
    gist: str
    grounding: Grounding
    anchors: tuple[str, ...]
    leaf: bool = True
    issues: int = 0


CONCEPTS: dict[str, _C] = {
    c.id: c
    for c in [
        _C("toyformer", "Toyformer", "A two-layer transformer small enough to read in one sitting.",
           "in_code", ("sec:1",), leaf=False),
        _C("self-attention", "Self-attention",
           "Each token builds its new representation as a weighted mix of every token.",
           "in_code", ("sec:2.1", "eq:1"), leaf=False),
        _C("positional-encoding", "Positional encoding",
           "Adds a position signal to each token, since attention alone ignores order.",
           "in_code", ("sec:2.2", "eq:3"), leaf=False),
        _C("feed-forward", "Position-wise feed-forward",
           "The same small two-layer network applied to every token separately.",
           "in_code", ("sec:2",)),
        _C("residual-connections", "Residual connections",
           "Each sublayer adds its output to its input, so signals pass through unchanged.",
           "in_library", ("sec:2",)),
        _C("layer-normalization", "Layer normalization",
           "Rescales each token's features to zero mean and unit variance.",
           "in_library", ("sec:2",), issues=1),
        _C("label-smoothing", "Label smoothing",
           "Trains against slightly softened targets instead of one-hot labels.",
           "concept_only", ("sec:3",)),
        _C("warmup", "Learning-rate warmup",
           "The learning rate rises linearly for the first steps, then decays.",
           "shown_by_running", ("sec:3", "eq:4")),
        _C("multi-head", "Multi-head attention",
           "Several attention functions run in parallel on smaller projections.",
           "unknown", ("sec:2.1",)),
        # inside self-attention
        _C("qkv", "Queries, keys and values",
           "Three learned projections of each token: what it asks, offers and carries.",
           "concept_only", ("sec:2.1",)),
        _C("scaled-dot-product", "Scaled dot-product",
           "Scores a query against every key by dot product, divided by the square root of d_k.",
           "in_code", ("eq:1",), leaf=False),
        _C("softmax", "Softmax", "Turns a row of scores into positive weights that sum to one.",
           "in_library", ("eq:2",)),
        _C("attention-weights", "Attention weights",
           "The softmax output: how much each token draws from every other token.",
           "shown_by_running", ("eq:1",)),
        # inside scaled dot-product
        _C("dot-product", "Dot product",
           "Sum of element-wise products; large when two vectors point the same way.",
           "concept_only", ("eq:1",)),
        _C("sqrt-dk-scaling", "Scaling by sqrt(d_k)",
           "Keeps scores from growing with the key size, so softmax doesn't saturate.",
           "in_code", ("eq:1",)),
        # inside positional encoding
        _C("sinusoids", "Sinusoids of different frequencies",
           "Each dimension is a sine or cosine of the position at its own wavelength.",
           "in_code", ("eq:3",)),
        _C("relative-offsets", "Relative offsets",
           "A fixed offset between positions is a linear function of their encodings.",
           "concept_only", ("sec:2.2",)),
    ]
}  # fmt: skip

# layer id → (center, members, links as (source, target, label, kind))
LAYERS: dict[str, tuple[str, list[str], list[tuple[str, str, str, LinkKind]]]] = {
    MASTER: (
        "toyformer",
        ["self-attention", "positional-encoding", "feed-forward", "residual-connections",
         "layer-normalization", "label-smoothing", "warmup", "multi-head"],
        [
            ("toyformer", "self-attention", "mixes tokens with", "uses"),
            ("toyformer", "feed-forward", "transforms tokens with", "uses"),
            ("toyformer", "residual-connections", "wraps sublayers in", "uses"),
            ("toyformer", "label-smoothing", "is trained with", "uses"),
            ("toyformer", "warmup", "is optimized with", "uses"),
            ("self-attention", "positional-encoding", "needs", "enables"),
            ("self-attention", "multi-head", "is split into", "part_of"),
            ("residual-connections", "layer-normalization", "is followed by", "other"),
        ],
    ),
    "self-attention": (
        "self-attention",
        ["qkv", "scaled-dot-product", "softmax", "attention-weights"],
        [
            ("self-attention", "qkv", "projects tokens into", "part_of"),
            ("qkv", "scaled-dot-product", "are scored by", "enables"),
            ("scaled-dot-product", "softmax", "is normalized by", "uses"),
            ("softmax", "attention-weights", "produces", "causes"),
        ],
    ),
    "scaled-dot-product": (
        "scaled-dot-product",
        ["dot-product", "sqrt-dk-scaling"],
        [
            ("scaled-dot-product", "dot-product", "is built on", "part_of"),
            ("scaled-dot-product", "sqrt-dk-scaling", "is stabilized by", "uses"),
        ],
    ),
    "positional-encoding": (
        "positional-encoding",
        ["sinusoids", "relative-offsets"],
        [
            ("positional-encoding", "sinusoids", "is made of", "part_of"),
            ("sinusoids", "relative-offsets", "make easy to learn", "enables"),
        ],
    ),
}  # fmt: skip

_ANCHORS: list[tuple[str, AnchorKind, str, str, str | None, int | None]] = [
    ("sec:1", "section", "§1", "Introduction", None, 1),
    ("sec:2", "section", "§2", "Model", None, 1),
    ("sec:2.1", "section", "§2.1", "Attention", "sec:2", 2),
    ("eq:1", "equation", "Eq. 1", r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}\left(\frac{QK^\top}{\sqrt{d_k}}\right)V", "sec:2.1", None),  # noqa: E501
    ("eq:2", "equation", "Eq. 2", r"\mathrm{softmax}(x)_i=\frac{e^{x_i}}{\sum_j e^{x_j}}", "sec:2.1", None),  # noqa: E501
    ("sec:2.2", "section", "§2.2", "Positional encoding", "sec:2", 2),
    ("eq:3", "equation", "Eq. 3", r"PE_{(pos,2i)}=\sin\left(pos/10000^{2i/d}\right)", "sec:2.2", None),  # noqa: E501
    ("sec:3", "section", "§3", "Training", None, 1),
    ("eq:4", "equation", "Eq. 4", r"lr=d^{-0.5}\min(step^{-0.5}, step\cdot w^{-1.5})", "sec:3", None),  # noqa: E501
]  # fmt: skip


def _depths() -> dict[str, int]:
    depth = {LAYERS[MASTER][0]: 0}
    frontier = [MASTER]
    while frontier:
        nxt: list[str] = []
        for layer_id in frontier:
            center, members, _links = LAYERS[layer_id]
            for cid in members:
                if cid not in depth:
                    depth[cid] = depth[center] + 1
                    if cid in LAYERS:
                        nxt.append(cid)
        frontier = nxt
    return depth


def demo_paper() -> PaperDoc:
    anchors = [
        Anchor(id=a, kind=k, label=lab, text=t, parent_id=p, order=i, level=lvl)
        for i, (a, k, lab, t, p, lvl) in enumerate(_ANCHORS)
    ]
    return PaperDoc(
        paper_id=DEMO_PAPER_ID,
        ref=PaperRef(source_kind="upload", title="Toyformer"),
        title="Toyformer: A Transformer You Can Read in One Sitting",
        authors=["Ada Example", "Grace Sample"],
        abstract="A two-layer transformer with every part spelled out, for learning how it works.",
        anchors=anchors,
        symbols={"d_k": "dimension of the keys", "d": "model width"},
    )


def _explanation(c: _C, path: list[str]) -> Explanation:
    sections = ExplainSections(
        what_it_is=f"{c.title}: {c.gist[0].lower()}{c.gist[1:]}",
        how_it_works=(
            "Take the queries $Q$ and keys $K$, score them with $QK^\\top/\\sqrt{d_k}$, "
            "normalize each row with a softmax, then mix the values $V$ with those weights."
            if c.id in ("self-attention", "scaled-dot-product", "softmax", "attention-weights")
            else f"In the toy model, {c.title.lower()} is applied the same way at every layer."
        ),
        why_its_here=f"Without {c.title.lower()}, the Toyformer loses one of its working parts.",
        where_it_appears=list(c.anchors),
        common_confusion=f"{c.title} is easy to over-read; it does one job and nothing more.",
    )
    body = "\n\n".join(
        [
            f"### What it is\n{sections.what_it_is}",
            f"### How it works\n{sections.how_it_works}",
            f"### Why it's here\n{sections.why_its_here}",
            f"### A common confusion\n{sections.common_confusion}",
        ]
    )
    return Explanation(
        concept_id=c.id,
        angle="default",
        path_context=path,
        gist=c.gist,
        sections=sections,
        body_markdown=body,
        anchors_cited=list(c.anchors),
        model="demo",
        created_at=_CREATED,
        word_count=len(body.split()),
    )


def seed_demo(db: Database) -> str:
    """Write (or overwrite) the demo paper, its map and its explanations. Returns the paper id."""
    chart, details = ChartStore(db), DetailStore(db)
    save_paper(db, demo_paper())
    depth = _depths()
    for c in CONCEPTS.values():
        chart.upsert_concept(
            DEMO_PAPER_ID,
            Concept(
                id=c.id,
                canonical_name=c.title.lower(),
                title=c.title,
                gist=c.gist,
                grounding=c.grounding,
                is_first_principle=c.leaf,
                depth=depth[c.id],
                anchors=list(c.anchors),
                has_layer=c.id in LAYERS,
                gen_state="detailed",
                issue_count_self=c.issues,
            ),
        )
    for layer_id, (center, members, links) in LAYERS.items():
        layer = Layer(
            id=layer_id,
            paper_id=DEMO_PAPER_ID,
            center=center,
            concepts=members,
            links=[
                Link(
                    id=f"{layer_id}:{s}->{t}",
                    layer_id=layer_id,
                    source=s,
                    target=t,
                    label=label,
                    kind=kind,
                )
                for s, t, label, kind in links
            ],
            gen_state="detailed",
        )
        chart.save_layer(layer)
        for link in layer.links:
            source, target = CONCEPTS[link.source].title, CONCEPTS[link.target].title
            details.save_edge(
                DEMO_PAPER_ID,
                EdgeExplanation(
                    link_id=link.id,
                    headline=f"{source} {link.label} {target.lower()}",
                    body_markdown=(
                        f"{source} relies on {target.lower()} at this level of the map. "
                        f"Remove {target.lower()} and {source.lower()} stops doing its job."
                    ),
                    why_it_matters=f"Reading {target.lower()} first makes {source.lower()} easier.",
                ),
            )
    for c in CONCEPTS.values():
        details.save_explanation(DEMO_PAPER_ID, _explanation(c, ["toyformer"]))
    return DEMO_PAPER_ID
