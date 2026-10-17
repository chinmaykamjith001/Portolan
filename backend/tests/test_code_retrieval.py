from __future__ import annotations

import subprocess
import sys
import time

import pytest

from portolan.code.embeddings import (
    HashEmbedder,
    default_embedder,
    embed_symbols,
    has_embeddings,
    load_vectors,
)
from portolan.code.index_store import IndexStore
from portolan.code.retrieval import Hit, fts_query, query_terms, retrieve
from portolan.store.db import Database
from tests.test_code_store import REPO, SHA, Def, attention_files, build_file, save_files

DECOYS = [
    Def("attention_mask", doc="Build the causal mask.", body=["return torch.tril(ones)"]),
    Def("dot", doc="Plain dot.", body=["return a @ b"]),
    Def("product_of_experts", body=["return prod(xs)"]),
    Def("scaled_init", doc="Scale initial weights.", body=["w.mul_(0.02)"]),
    Def("dropout", body=["return F.dropout(x, p)"]),
    Def("layer_norm", doc="Layer normalisation.", body=["return ln(x)"]),
    Def("positional_encoding", doc="Sinusoidal positions.", body=["return pe"]),
    Def("Embeddings", kind="class", doc="Token embeddings scaled by sqrt(d_model)."),
    Def("make_model", doc="Build the full encoder-decoder.", body=["return EncoderDecoder()"]),
    Def("greedy_decode", body=["return ys"]),
    Def("subsequent_mask", doc="Mask out subsequent positions.", body=["return mask"]),
    Def("clones", doc="Produce N identical layers.", body=["return ModuleList()"]),
    Def("rate", doc="Learning rate schedule.", body=["return factor * step"]),
    Def("data_gen", doc="Random data for a copy task.", body=["yield Batch()"]),
    Def("Generator", kind="class", doc="Linear + softmax generation step."),
    Def("SublayerConnection", kind="class", doc="Residual connection then layer norm."),
    Def("EncoderLayer", kind="class", doc="Self-attention and feed forward."),
    Def("DecoderLayer", kind="class", doc="Self-attention, source attention, feed forward."),
    Def("run_epoch", doc="Standard training loop.", body=["loss = loss_compute(out)"]),
    Def("product_key_attention", doc="Attention over product keys.", body=["return topk"]),
    Def("dot_product_score", doc="Unscaled score.", body=["return q @ k.T"]),
]
TARGET = Def(
    "scaled_dot_product_attention",
    doc="Compute softmax(Q K^T / sqrt(d_k)) V.",
    body=["scores = q @ k.T / math.sqrt(d_k)", "return softmax(scores) @ v"],
)


@pytest.fixture
def store(db: Database) -> IndexStore:
    s = IndexStore(db)
    layers = build_file("model/layers.py", [*DECOYS[:10], TARGET, *DECOYS[10:]])
    save_files(s, [*attention_files(), layers])
    return s


def names(hits: list[Hit]) -> list[str]:
    return [h.symbol.name for h in hits]


def test_query_terms_split_identifiers() -> None:
    assert query_terms("MultiHeadAttention") == [
        "multiheadattention", "multi", "head", "attention", "multi_head_attention",
    ]  # fmt: skip
    assert query_terms("the label_smoothing") == [
        "label_smoothing", "label", "smoothing", "labelsmoothing",
    ]  # fmt: skip
    assert query_terms("the of") == ["the", "of", "the_of", "theof"]  # only stopwords: keep them
    assert query_terms("") == []
    assert fts_query("  ...  ") is None
    assert fts_query('x"y') == '"x" OR "y" OR "x_y" OR "x_y"* OR "xy"'


def test_partial_word_matches(store: IndexStore) -> None:
    assert "MultiHeadAttention" in names(retrieve(store, REPO, SHA, "attention"))
    assert names(retrieve(store, REPO, SHA, "smoothing"))[0] == "label_smoothing"
    assert names(retrieve(store, REPO, SHA, "multi head"))[0] == "MultiHeadAttention"
    assert names(retrieve(store, REPO, SHA, "MultiHeadAttention"))[0] == "MultiHeadAttention"
    assert names(retrieve(store, REPO, SHA, "train"))[0] == "train_step"  # prefix match
    assert names(retrieve(store, REPO, SHA, "label-smoothing"))[0] == "label_smoothing"


@pytest.mark.parametrize(
    "query",
    [
        "a.b(c",
        '"',
        '"unterminated',
        "'",
        "NEAR(x y)",
        "attention AND OR NOT",
        "x:y",
        "col:attention",
        "^start",
        "*",
        "a*b",
        "(((",
        "{}[]",
        "-",
        "--label-smoothing",
        "\x00",
        "ünïcödé attention",
        "注意力",
        "x" * 5_000,
        " ".join(f"w{i}" for i in range(500)),
    ],
)
def test_hostile_queries_never_raise(store: IndexStore, query: str) -> None:
    hits = retrieve(store, REPO, SHA, query)
    assert isinstance(hits, list)
    retrieve(store, REPO, SHA, query, embedder=HashEmbedder(), kinds={"function"})


def test_empty_and_degenerate_inputs(store: IndexStore) -> None:
    assert retrieve(store, REPO, SHA, "") == []
    assert retrieve(store, REPO, SHA, "   \n\t") == []
    assert retrieve(store, REPO, SHA, "!!!") == []
    assert retrieve(store, REPO, SHA, "attention", k=0) == []
    assert retrieve(store, REPO, SHA, "attention", kinds=set()) == []
    assert retrieve(store, "github:other/repo", SHA, "attention") == []
    assert retrieve(store, REPO, "f" * 40, "attention") == []


def test_ranking_sanity(store: IndexStore) -> None:
    hits = retrieve(store, REPO, SHA, "scaled dot product attention")
    assert names(hits)[0] == "scaled_dot_product_attention"
    assert len(hits) == 10
    assert all(h.cosine is None and h.bm25 is not None and h.bm25 > 0 for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_kinds_filter(store: IndexStore) -> None:
    hits = retrieve(store, REPO, SHA, "attention", kinds={"class"})
    assert hits
    assert {h.symbol.kind for h in hits} == {"class"}
    assert "scaled_dot_product_attention" not in names(hits)


def test_embeddings_stored_and_scoped(store: IndexStore) -> None:
    embedder = HashEmbedder()
    assert not has_embeddings(store, REPO, SHA, embedder.model)
    n = embed_symbols(store, REPO, SHA, embedder, batch=7)
    assert n == len(store.symbols(REPO, SHA))
    assert has_embeddings(store, REPO, SHA, embedder.model)
    assert not has_embeddings(store, REPO, SHA, "BAAI/bge-small-en-v1.5")
    vectors = load_vectors(store, REPO, SHA, embedder.model, embedder.dim)
    v = vectors["model/layers.py::scaled_dot_product_attention"]
    assert len(v) == embedder.dim
    assert sum(x * x for x in v) == pytest.approx(1.0, abs=1e-5)
    assert embed_symbols(store, REPO, SHA, embedder) == n  # re-embedding replaces, not duplicates
    assert len(load_vectors(store, REPO, SHA, embedder.model, embedder.dim)) == n

    # A re-save of the index drops vectors that described the old symbols.
    save_files(store, attention_files())
    assert not has_embeddings(store, REPO, SHA, embedder.model)


def test_hash_embedder_is_deterministic() -> None:
    a, b = HashEmbedder(64), HashEmbedder(64)
    assert a.embed(["MultiHeadAttention forward"]) == b.embed(["multi head attention forward"])
    assert a.embed([""]) == [[0.0] * 64]
    assert a.model == "hash-bow-64"


def test_hybrid_without_vectors_is_bm25_order(store: IndexStore) -> None:
    query = "scaled dot product attention"
    plain = retrieve(store, REPO, SHA, query)
    with_embedder = retrieve(store, REPO, SHA, query, embedder=HashEmbedder())
    assert names(with_embedder) == names(plain)
    assert all(h.cosine is None for h in with_embedder)


def test_hybrid_fusion_with_hash_embedder(store: IndexStore) -> None:
    embedder = HashEmbedder()
    embed_symbols(store, REPO, SHA, embedder)
    query = "scaled dot product attention"
    plain = names(retrieve(store, REPO, SHA, query, k=20))
    hits = retrieve(store, REPO, SHA, query, k=20, embedder=embedder)
    fused = names(hits)

    assert fused != plain  # the vectors moved something …
    assert fused[0] == plain[0] == "scaled_dot_product_attention"  # … but not the clear winner
    # MultiHeadAttention shares "attention" in name, docstring and body, so it climbs; Embeddings
    # only matched "scaled" in its docstring, so it falls.
    assert fused.index("MultiHeadAttention") < plain.index("MultiHeadAttention")
    assert fused.index("Embeddings") > plain.index("Embeddings")
    assert all(h.cosine is not None for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert 0 < hits[0].score <= 2 / (60 + 1)  # RRF: at most first place in both lists

    # Symbols with no shared signal are not padded in from the vector side.
    assert names(retrieve(store, REPO, SHA, "mask", k=20, embedder=embedder)) == [
        "subsequent_mask",
        "attention_mask",
    ]


class ConceptEmbedder:
    """Maps synonyms onto shared axes, so it finds what BM25 cannot: a paraphrase."""

    model = "concept-test"
    dim = 3
    _axes = (("softmax", "exponential"), ("mask", "causal"), ("dropout", "regularise"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            low = text.lower()
            v = [float(sum(low.count(w) for w in axis)) for axis in self._axes]
            norm = sum(x * x for x in v) ** 0.5
            out.append([x / norm for x in v] if norm else [0.0, 0.0, 1e-9])
        return out


def test_hybrid_finds_paraphrase_bm25_misses(db: Database) -> None:
    store = IndexStore(db)
    f = build_file(
        "ops.py",
        [
            Def("normalise_scores", doc="Normalised exponential of the logits.", body=["..."]),
            Def("softmax_temperature", doc="Temperature for softmax.", body=["return t"]),
            Def("causal", doc="Lower triangle.", body=["return tril"]),
        ],
    )
    save_files(store, [f])
    embedder = ConceptEmbedder()
    embed_symbols(store, REPO, SHA, embedder)

    assert names(retrieve(store, REPO, SHA, "softmax")) == ["softmax_temperature"]
    fused = retrieve(store, REPO, SHA, "softmax", embedder=embedder)
    assert names(fused) == ["softmax_temperature", "normalise_scores"]  # "causal": no signal
    paraphrase = fused[1]
    assert paraphrase.bm25 is None
    assert paraphrase.cosine == pytest.approx(1.0)


def test_rerank_hook_sees_all_candidates(store: IndexStore) -> None:
    seen: list[int] = []

    def reverse(query: str, hits: list[Hit]) -> list[Hit]:
        assert query == "attention"
        seen.append(len(hits))
        return list(reversed(hits))

    plain = retrieve(store, REPO, SHA, "attention", k=50)
    reranked = retrieve(store, REPO, SHA, "attention", k=3, rerank=reverse)
    assert seen == [len(plain)]
    assert names(reranked) == names(list(reversed(plain)))[:3]


def test_default_embedder_is_none_and_fast() -> None:
    started = time.perf_counter()
    assert default_embedder() is None
    assert time.perf_counter() - started < 1.0
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules


def test_modules_do_not_import_heavy_packages() -> None:
    code = (
        "import sys, portolan.code.retrieval, portolan.code.embeddings;"
        "heavy = {'sentence_transformers', 'torch', 'transformers'} & set(sys.modules);"
        "print(sorted(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert out.stdout.strip() == "[]"
