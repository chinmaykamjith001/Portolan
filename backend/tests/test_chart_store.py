from __future__ import annotations

import pytest

from portolan.chart.canonical import Canonicalizer, canonical_name, concept_slug, similarity
from portolan.chart.issues import issues_inside, nearest_ancestor_by_anchor
from portolan.models.concept import MASTER, Concept, Layer, Link
from portolan.store.chart import ChartStore
from portolan.store.db import Database

P = "paper-1"


def concept(cid: str, title: str | None = None, depth: int = 1, **kw: object) -> Concept:
    title = title or cid.replace("-", " ")
    return Concept.model_validate(
        {
            "id": cid,
            "canonical_name": canonical_name(title),
            "title": title,
            "gist": f"{title} gist.",
            "depth": depth,
            **kw,
        }
    )


def layer(center: str, members: list[str]) -> Layer:
    links = [
        Link(id=f"{center}:{center}->{m}", layer_id=center, source=center, target=m, label="uses")
        for m in members
    ]
    return Layer(id=center, paper_id=P, center=center, concepts=members, links=links)


@pytest.fixture
def store(db: Database) -> ChartStore:
    return ChartStore(db)


# -- store -------------------------------------------------------------------------------------


def test_concept_round_trip(store: ChartStore) -> None:
    c = concept("softmax", "Softmax", anchors=["s3.2"], grounding="in_code")
    store.upsert_concept(P, c)
    assert store.get_concept(P, "softmax") == c
    assert store.get_concept(P, "missing") is None
    assert store.get_concept("other-paper", "softmax") is None
    assert store.find_by_canonical(P, "softmax") == c
    c2 = c.model_copy(update={"gist": "Updated."})
    store.upsert_concept(P, c2)
    assert store.get_concept(P, "softmax") == c2


def test_concepts_filter_and_order(store: ChartStore) -> None:
    for cid in ("b", "a", "c"):
        store.upsert_concept(P, concept(cid))
    assert list(store.concepts(P)) == ["a", "b", "c"]
    assert list(store.concepts(P, ["c", "a", "nope"])) == ["c", "a"]
    assert store.concepts(P, []) == {}


def test_layer_round_trip_dag_and_has_layer(store: ChartStore) -> None:
    store.upsert_concept(P, concept("paper", depth=0))
    for cid in ("attention", "ffn"):
        store.upsert_concept(P, concept(cid, depth=1))
    store.upsert_concept(P, concept("softmax", depth=2))
    master = Layer(id=MASTER, paper_id=P, center="paper", concepts=["attention", "ffn"], links=[])
    store.save_layer(master)
    store.save_layer(layer("attention", ["softmax"]))
    store.save_layer(layer("ffn", ["softmax"]))

    assert store.get_layer(P, "attention") == layer("attention", ["softmax"])
    assert store.get_layer(P, "nope") is None
    assert store.layer_ids(P) == ["attention", "ffn", MASTER]
    assert store.layers_containing(P, "softmax") == ["attention", "ffn"]
    assert store.layers_containing(P, "attention") == ["attention", MASTER]

    attention = store.get_concept(P, "attention")
    paper = store.get_concept(P, "paper")
    softmax = store.get_concept(P, "softmax")
    assert attention and attention.has_layer
    assert paper and not paper.has_layer  # master layer does not mark its center
    assert softmax and not softmax.has_layer

    assert store.children(P, "attention") == ["softmax"]
    assert store.children(P, MASTER) == ["attention", "ffn"]
    assert store.children(P, "softmax") == []


def test_save_layer_replaces_members(store: ChartStore) -> None:
    store.save_layer(layer("attention", ["softmax", "dot-product"]))
    store.save_layer(layer("attention", ["dot-product"]))
    assert store.layers_containing(P, "softmax") == []
    assert store.layers_containing(P, "dot-product") == ["attention"]


def test_delete_paper(store: ChartStore) -> None:
    store.upsert_concept(P, concept("attention"))
    store.upsert_concept("keep", concept("attention"))
    store.save_layer(layer("attention", ["softmax"]))
    store.delete_paper(P)
    assert store.concepts(P) == {}
    assert store.layer_ids(P) == []
    assert store.layers_containing(P, "softmax") == []
    assert store.get_concept("keep", "attention") is not None


# -- canonical names ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Softmax", "softmax"),
        ("An Attention", "attention"),
        ("Attentions", "attention"),
        ("Attention matrices", "attention matrix"),
        ("Probabilities", "probability"),
        ("Positional encodings", "positional encoding"),
        ("Loss", "loss"),
        ("Softmax bias", "softmax bias"),
        ("Gaussian process", "gaussian process"),
        ("Image patches", "image patch"),
        ("Self-Attention", "self attention"),
        ("self attention", "self attention"),
        ("LayerNorm", "layer normalization"),
        ("Layer Norm", "layer normalization"),
        ("Layer normalization", "layer normalization"),
        ("Pre-LayerNorm", "pre layer normalization"),
        ("ReLU", "rectified linear unit"),
        ("ReLUs", "rectified linear unit"),
        ("MLP", "multilayer perceptron"),
        ("Multi-layer perceptron", "multilayer perceptron"),
        ("  Scaled   dot-product attention.  ", "scaled dot product attention"),
        ("Bayes' rule", "bayes rule"),
    ],
)
def test_canonical_name(title: str, expected: str) -> None:
    assert canonical_name(title) == expected


def test_layernorm_is_not_rmsnorm() -> None:
    assert canonical_name("RMSNorm") == canonical_name("RMS norm") == "rms normalization"
    assert canonical_name("RMSNorm") != canonical_name("LayerNorm")
    assert canonical_name("Root mean square layer normalization") == "rms normalization"


def test_slug_and_similarity() -> None:
    assert concept_slug("Scaled dot-product attention") == "scaled-dot-product-attention"
    assert concept_slug("!!!") == "concept"
    long = concept_slug("word " * 30)
    assert len(long) <= 60 and not long.endswith("-")
    assert similarity("Softmax", "softmax") == 1.0
    assert similarity("multi-head attention", "multi head attentions") == 1.0
    assert 0.5 < similarity("multi-head attention", "multi-headed attention") < 1.0
    assert similarity("softmax", "convolution") < 0.5
    assert 0.0 <= similarity("", "x") <= 1.0


# -- resolver ----------------------------------------------------------------------------------


async def test_resolve_exact(store: ChartStore) -> None:
    store.upsert_concept(P, concept("layer-norm", "LayerNorm"))
    canon = Canonicalizer(store, P)
    assert await canon.resolve("Layer normalization", "x") == "layer-norm"
    assert await canon.resolve("RMSNorm", "x") is None


async def test_resolve_near_with_judge(store: ChartStore) -> None:
    store.upsert_concept(P, concept("sdpa", "Scaled dot-product attention"))
    calls: list[tuple[tuple[str, str], tuple[str, str]]] = []

    async def yes(a: tuple[str, str], b: tuple[str, str]) -> bool:
        calls.append((a, b))
        return True

    async def no(a: tuple[str, str], b: tuple[str, str]) -> bool:
        return False

    title = "Scaled dot product attention mechanism"
    assert similarity(title, "Scaled dot-product attention") >= 0.82
    assert await Canonicalizer(store, P, judge=yes).resolve(title, "g") == "sdpa"
    assert calls == [
        ((title, "g"), ("Scaled dot-product attention", "Scaled dot-product attention gist."))
    ]
    assert await Canonicalizer(store, P, judge=no).resolve(title, "g") is None
    # Far-apart names never reach the judge.
    calls.clear()
    assert await Canonicalizer(store, P, judge=yes).resolve("Convolution", "g") is None
    assert calls == []


async def test_resolve_near_without_judge(store: ChartStore) -> None:
    store.upsert_concept(P, concept("sdpa", "Scaled dot-product attention"))
    assert (
        await Canonicalizer(store, P).resolve("Scaled dot product attention mechanism", "g") is None
    )


async def test_resolve_registered_in_batch(store: ChartStore) -> None:
    canon = Canonicalizer(store, P)
    assert await canon.resolve("Softmax", "g") is None
    canon.register(concept(canon.new_id("Softmax"), "Softmax"))
    assert await canon.resolve("the softmax", "g") == "softmax"

    async def yes(a: tuple[str, str], b: tuple[str, str]) -> bool:
        return True

    canon2 = Canonicalizer(store, P, judge=yes)
    canon2.register(concept("sdpa", "Scaled dot-product attention"))
    assert await canon2.resolve("Scaled dot product attention mechanism", "g") == "sdpa"


def test_new_id_unique(store: ChartStore) -> None:
    store.upsert_concept(P, concept("softmax", "Softmax"))
    canon = Canonicalizer(store, P)
    assert canon.new_id("Softmax") == "softmax-2"
    assert canon.new_id("Softmax") == "softmax-3"
    canon.register(concept("attention", "Attention"))
    assert canon.new_id("Attention") == "attention-2"
    assert canon.new_id("Dot product") == "dot-product"
    long_id = canon.new_id("x" * 80)
    assert long_id == "x" * 60
    assert canon.new_id("x" * 80) == "x" * 58 + "-2"


# -- issues ------------------------------------------------------------------------------------


def test_issues_inside_diamond() -> None:
    graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
    findings = {"a": ["f-own"], "b": ["f1"], "c": ["f1", "f2"], "d": ["f3"]}
    expanded: list[str] = []

    def children(cid: str) -> list[str]:
        expanded.append(cid)
        return graph.get(cid, [])

    assert issues_inside(children, lambda c: findings.get(c, []), "a") == 3
    assert sorted(expanded) == ["a", "b", "c", "d"]  # d expanded once despite two parents
    assert issues_inside(children, lambda c: findings.get(c, []), "d") == 0


def test_issues_inside_cycle() -> None:
    graph = {"a": ["b"], "b": ["c"], "c": ["a", "b"]}
    findings = {"a": ["fa"], "b": ["fb"], "c": ["fc"]}
    assert issues_inside(lambda c: graph[c], lambda c: findings[c], "a") == 2
    assert issues_inside(lambda c: graph[c], lambda c: findings[c], "b") == 2


def test_nearest_ancestor_by_anchor() -> None:
    concepts = {
        "attention": concept("attention", depth=1, anchors=["s3", "s3.2"]),
        "softmax": concept("softmax", depth=2, anchors=["s3.2"]),
        "scaling": concept("scaling", depth=2, anchors=["s3.2", "eq1"]),
        "pending": concept("pending", depth=3, anchors=["eq9"], gen_state="generating"),
        "ffn": concept("ffn", depth=1, anchors=["s3.3"]),
    }
    parents = {"softmax": "attention", "scaling": "attention", "pending": "ffn"}

    def parent_of(cid: str) -> str | None:
        return parents.get(cid)

    # Deepest wins; between equally deep matches the larger overlap, then the smaller id.
    assert nearest_ancestor_by_anchor({"s3.2", "eq1"}, concepts, parent_of) == "scaling"
    assert nearest_ancestor_by_anchor({"s3.2"}, concepts, parent_of) == "scaling"
    assert nearest_ancestor_by_anchor({"s3"}, concepts, parent_of) == "attention"
    # An ungenerated match lifts to its nearest generated ancestor.
    assert nearest_ancestor_by_anchor({"eq9"}, concepts, parent_of) == "ffn"
    assert nearest_ancestor_by_anchor({"nowhere"}, concepts, parent_of) is None
    assert nearest_ancestor_by_anchor(set(), concepts, parent_of) is None
