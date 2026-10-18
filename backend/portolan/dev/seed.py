"""A small, fixed demo paper written straight into the database: no network, no LLM.

Used by the frontend end-to-end tests (Playwright) and handy for UI work. Every concept without
a child layer is a first principle, so opening a layer never queues LLM work; every concept has
a default explanation and every link an edge explanation, so the drawer and the edge popover
always have content. The content is hand-written for the toy transformer in the test fixtures.

The detail tabs have content too, so the drawer never queues LLM work in demos and tests:
- Code: Scaled dot-product (every note kind but a mismatch), Layer normalization (a planted
  mismatch), Softmax (library call), and a "not in the code" grounding for every concept that
  has no code.
- Example: Softmax (two variants, the second an overflow edge case), Scaling by sqrt(d_k) and
  Attention weights (from a repo run). Every number is computed here with `math`, never typed.
- Graph: Softmax (bar), Attention weights (heatmap), Scaling by sqrt(d_k) (before/after) and
  Sinusoids (line); every other concept records "no graph" so the Graph tab stays hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from portolan.dev.seed_faq import seed_faq
from portolan.dev.seed_findings import seed_findings
from portolan.dev.seed_route import seed_route
from portolan.llm.prompts.detail_schemas import ExplainSections
from portolan.models.code import CodeSpan, LineNote
from portolan.models.concept import MASTER, Concept, Grounding, Layer, Link, LinkKind
from portolan.models.detail import CodeGrounding, EdgeExplanation, Example, Explanation, GraphSpec
from portolan.models.paper import Anchor, AnchorKind, PaperDoc, PaperRef
from portolan.models.repo import RepoCandidate, RepoStatus
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.details import DetailStore
from portolan.store.papers import save_paper
from portolan.store.repos import choose_repo, save_candidates

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


# -- code, examples, graphs ---------------------------------------------------------------------

REPO_ID = "github:ada-example/toyformer"
COMMIT = "5e1f0c7d3a9b4c2e8f6a1d0b7c3e9f5a2b4d6c8e"
REPO_URL = "https://github.com/ada-example/toyformer"

_ATTENTION_PY = [
    "def scaled_dot_product_attention(query, key, value, mask=None):",
    '    """Score queries against keys, scale by sqrt(d_k), softmax, and weight the values."""',
    "    d_k = query.size(-1)",
    "    scores = torch.matmul(query, key.transpose(-2, -1)) / math.sqrt(d_k)",
    "    if mask is not None:",
    "        scores = scores.masked_fill(mask == 0, -1e9)",
    "    weights = F.softmax(scores, dim=-1)",
    "    return torch.matmul(weights, value), weights",
]
_LAYERS_PY = [
    "class SublayerConnection(nn.Module):",
    "    def __init__(self, d_model: int, dropout: float = 0.1):",
    "        super().__init__()",
    "        self.norm = nn.LayerNorm(d_model, eps=1e-5)",
    "        self.dropout = nn.Dropout(dropout)",
    "",
    "    def forward(self, x, sublayer):",
    "        # normalize first, then run the sublayer and add the input back",
    "        return x + self.dropout(sublayer(self.norm(x)))",
]


def _file(path: str, first: int, lines: list[str]) -> dict[str, dict[int, str]]:
    return {path: {first + i: text for i, text in enumerate(lines)}}


def _span(path: str, start: int, end: int) -> CodeSpan:
    return CodeSpan(repo_id=REPO_ID, commit_sha=COMMIT, path=path, start_line=start, end_line=end)


def _link(path: str) -> dict[str, str]:
    return {path: f"{REPO_URL}/blob/{COMMIT}/{path}"}


def groundings() -> list[CodeGrounding]:
    attn, layers = "toyformer/attention.py", "toyformer/layers.py"
    out = [
        CodeGrounding(
            concept_id="scaled-dot-product",
            grounding="in_code",
            spans=[_span(attn, 11, 18)],
            notes=[
                LineNote(
                    line=13,
                    tag="d_k",
                    note="The key size, read from the last dimension.",
                    kind="context",
                ),
                LineNote(
                    line=14,
                    tag="Eq. 1 · QKᵀ/√dₖ",
                    kind="implements",
                    note="Scores every query against every key, then divides by √dₖ.",
                ),
                LineNote(
                    line=16,
                    tag="padding mask",
                    kind="detail_not_in_paper",
                    note="Masked scores become -1e9 so softmax gives them no weight; "
                    "the paper never says how.",
                ),
                LineNote(
                    line=17,
                    tag="Eq. 1 · softmax",
                    kind="implements",
                    note="Turns each row of scores into weights that sum to 1.",
                ),
            ],
            library_calls=["torch.matmul", "torch.nn.functional.softmax"],
            summary="Lines 11-18 implement Eq. 1.",
            code=_file(attn, 11, _ATTENTION_PY),
            links=_link(attn),
        ),
        CodeGrounding(
            concept_id="layer-normalization",
            grounding="in_library",
            spans=[_span(layers, 20, 28)],
            notes=[
                LineNote(
                    line=23,
                    tag="§2 · LayerNorm",
                    kind="implements",
                    note="Normalizes each token's features with torch's LayerNorm.",
                ),
                LineNote(
                    line=23,
                    tag="eps",
                    kind="detail_not_in_paper",
                    note="eps=1e-5 keeps the division stable; the paper gives no value.",
                ),
                LineNote(
                    line=28,
                    tag="§2 · post-norm",
                    kind="mismatch",
                    note="The paper normalizes after the residual add; here the input is "
                    "normalized before the sublayer (pre-norm).",
                ),
            ],
            library_calls=["torch.nn.LayerNorm"],
            summary="Lines 20-28 wrap each sublayer in a residual connection with LayerNorm.",
            code=_file(layers, 20, _LAYERS_PY),
            links=_link(layers),
        ),
        CodeGrounding(
            concept_id="softmax",
            grounding="in_library",
            spans=[_span(attn, 17, 17)],
            notes=[
                LineNote(
                    line=17,
                    tag="Eq. 2",
                    kind="implements",
                    note="One library call; torch subtracts the row max first, which the "
                    "paper doesn't mention.",
                ),
            ],
            library_calls=["torch.nn.functional.softmax"],
            summary="Line 17 calls torch's softmax; no softmax is written in this repo.",
            code=_file(attn, 17, [_ATTENTION_PY[6]]),
            links=_link(attn),
        ),
    ]
    with_code = {g.concept_id for g in out}
    for c in CONCEPTS.values():
        if c.grounding == "concept_only" and c.id not in with_code:
            out.append(CodeGrounding(concept_id=c.id, grounding="concept_only"))
    return out


def _softmax(scores: list[float]) -> list[float]:
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    return [e / sum(exps) for e in exps]


def _naive_softmax(scores: list[float]) -> list[float]:
    try:
        exps = [math.exp(s) for s in scores]
    except OverflowError:
        return [math.nan] * len(scores)
    return [e / sum(exps) for e in exps]


def examples() -> list[Example]:
    z = [2.0, 1.0, 0.1]
    big = [1000.0, 1001.0]
    stable, naive = _softmax(big), _naive_softmax(big)
    sizes = [4, 16, 64, 256]
    heads = [[1.0, 0.0, 0.0], [0.5, 1.0, 0.0], [0.0, 0.5, 1.0]]
    eps, k = 0.1, 5

    def smooth(true_class: bool) -> float:
        return (1 - eps) + eps / k if true_class else eps / k

    return [
        Example(
            concept_id="softmax",
            variant=0,
            source_label="paper_notation",
            caption="softmax([2.0, 1.0, 0.1])",
            program="import math, json\nz = [2.0, 1.0, 0.1]\n...",
            columns=["score", "weight"],
            rows=[[f"{a:.1f}", f"{b:.3f}"] for a, b in zip(z, _softmax(z), strict=True)],
            note=f"The weights sum to {sum(_softmax(z)):.0f}; the biggest score takes the most.",
        ),
        Example(
            concept_id="softmax",
            variant=1,
            source_label="paper_notation",
            caption="softmax([1000, 1001]): why libraries subtract the max",
            program="import math, json\nz = [1000.0, 1001.0]\n...",
            columns=["method", "w₁", "w₂"],
            rows=[
                ["naive exp(z)/Σexp(z)", *[f"{v:.3f}" for v in naive]],
                ["subtract the max first", *[f"{v:.3f}" for v in stable]],
            ],
            note="exp(1000) overflows, so the naive version gives nan; shifting by the max "
            "leaves the answer unchanged.",
        ),
        Example(
            concept_id="label-smoothing",
            variant=0,
            source_label="paper_notation",
            caption="Target for class 2 of 5, with and without label smoothing (ε = 0.1)",
            program="import json\neps, k = 0.1, 5\n...",
            columns=["class", "one-hot", "smoothed"],
            rows=[
                [str(c + 1), f"{1.0 if c == 1 else 0.0:.2f}", f"{smooth(c == 1):.2f}"]
                for c in range(5)
            ],
            note=f"The true class keeps {smooth(True):.2f}; "
            f"the other four share {smooth(False) * 4:.2f}.",
        ),
        Example(
            concept_id="sqrt-dk-scaling",
            variant=0,
            source_label="paper_notation",
            caption="q·k for all-ones vectors, with and without dividing by √d_k",
            program="import math, json\nfor d_k in (4, 16, 64, 256): ...",
            columns=["d_k", "q·k", "q·k / √d_k"],
            rows=[[str(d), f"{float(d):.1f}", f"{d / math.sqrt(d):.1f}"] for d in sizes],
            note="Unscaled scores grow with d_k, which would push softmax toward a hard max.",
        ),
        Example(
            concept_id="attention-weights",
            variant=0,
            source_label="from_repo_run",
            caption="Weights from the repo's attention on a 3-token toy input",
            program="from toyformer.attention import scaled_dot_product_attention\n...",
            columns=["query", "token 1", "token 2", "token 3"],
            rows=[
                [f"token {i + 1}", *[f"{w:.2f}" for w in _softmax(row)]]
                for i, row in enumerate(heads)
            ],
            note="Each row sums to 1: how much that token reads from every token.",
        ),
    ]


def graphs() -> list[GraphSpec]:
    z = [2.0, 1.0, 0.1]
    sizes = [4, 16, 64, 256]
    freqs = list(range(0, 64, 4))
    return [
        GraphSpec(
            concept_id="softmax",
            template="bar",
            caption="softmax([2.0, 1.0, 0.1]): scores to weights",
            data={
                "x": ["2.0", "1.0", "0.1"],
                "series": [
                    {"name": "score", "y": z},
                    {"name": "weight", "y": [round(w, 3) for w in _softmax(z)]},
                ],
                "x_label": "score",
                "y_label": "value",
            },
            note="Bigger scores take a bigger share, and the shares add up to 1.",
        ),
        GraphSpec(
            concept_id="attention-weights",
            template="heatmap",
            caption="Who reads from whom (3-token toy input)",
            data={
                "rows": ["token 1", "token 2", "token 3"],
                "cols": ["token 1", "token 2", "token 3"],
                "values": [
                    [round(w, 2) for w in _softmax(row)]
                    for row in [[1.0, 0.0, 0.0], [0.5, 1.0, 0.0], [0.0, 0.5, 1.0]]
                ],
                "x_label": "read from",
                "y_label": "token",
            },
            note="Each row sums to 1.",
        ),
        GraphSpec(
            concept_id="sqrt-dk-scaling",
            template="before_after",
            caption="Typical size of a score, by key size",
            data={
                "labels": [f"d_k={d}" for d in sizes],
                "before": [math.sqrt(d) for d in sizes],
                "after": [1.0 for _ in sizes],
                "before_name": "without scaling",
                "after_name": "divided by √d_k",
                "y_label": "score std",
            },
            note="With unit-variance entries, q·k has standard deviation √d_k.",
        ),
        GraphSpec(
            concept_id="sinusoids",
            template="line",
            caption="One position-encoding dimension across positions",
            data={
                "x": freqs,
                "series": [
                    {"name": "sin(pos/10)", "y": [round(math.sin(p / 10), 3) for p in freqs]},
                    {"name": "sin(pos/3)", "y": [round(math.sin(p / 3), 3) for p in freqs]},
                ],
                "x_label": "position",
                "y_label": "value",
            },
            note="Each dimension oscillates at its own wavelength.",
        ),
    ]


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

    repo = RepoCandidate(
        id=REPO_ID, url=REPO_URL, owner="ada-example", name="toyformer", source="paper_link",
        official=True, official_evidence="linked in the paper", status="ok",
    )  # fmt: skip
    status = RepoStatus(kind="official_found", message="Official code found: ada-example/toyformer")
    save_candidates(db, DEMO_PAPER_ID, [repo], status)
    choose_repo(db, DEMO_PAPER_ID, repo)
    for g in groundings():
        details.save_grounding(DEMO_PAPER_ID, g)
    for e in examples():
        details.save_example(DEMO_PAPER_ID, e)
    graph_for = {g.concept_id: g for g in graphs()}
    for c in CONCEPTS.values():
        details.save_graph(DEMO_PAPER_ID, c.id, graph_for.get(c.id))  # None = "no graph"
    seed_route(db, DEMO_PAPER_ID)
    seed_findings(db, DEMO_PAPER_ID)
    seed_faq(db, DEMO_PAPER_ID)
    return DEMO_PAPER_ID
