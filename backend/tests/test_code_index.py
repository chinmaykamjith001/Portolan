"""M3 acceptance: index the tiny fixture repo and retrieve known concepts in the top 3."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from portolan.code.embeddings import HashEmbedder, embed_symbols
from portolan.code.index_store import IndexStore
from portolan.code.indexer import build_index, index_checkout, library_calls, walk
from portolan.code.retrieval import retrieve
from portolan.store.db import Database

FIXTURE = Path(__file__).parent / "fixtures" / "repos" / "tiny_transformer"
REPO, SHA = "local:fixture", "content:fixture"


@pytest.fixture
def store(db: Database) -> IndexStore:
    s = IndexStore(db)
    index_checkout(s, FIXTURE, REPO, SHA)
    return s


def test_walk_skips_vendored_and_big_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    (root / "big.py").write_text("x = 1\n" * 200_000)  # ~1.2 MB
    (root / "notes.txt").write_text("not indexed")
    found, skipped = walk(root)
    paths = [rel for rel, _, _ in found]
    assert "toyformer/attention.py" in paths and "configs/base.yaml" in paths
    assert not any(p.startswith("third_party/") for p in paths)
    assert skipped["third_party/"] == "vendored or generated folder"
    assert skipped["big.py"] == "larger than 1 MB"
    assert "notes.txt" not in paths


def test_index_contents_and_libraries() -> None:
    built = build_index(FIXTURE, REPO, SHA)
    idx = built.index
    assert set(idx.files) >= {"toyformer/attention.py", "toyformer/layers.py", "toyformer/train.py",
                              "configs/base.yaml"}  # fmt: skip
    assert idx.library_modules == ["torch"]  # math/argparse are stdlib; .attention is the repo
    symbols = {s.qualname: s for f in built.files for s in f.symbols}
    assert symbols["MultiHeadAttention.forward"].kind == "method"
    assert symbols["scaled_dot_product_attention"].start_line == 10
    assert symbols["model.d_model"].value == "512"
    assert symbols["train.label_smoothing"].value == "0.0"
    assert any(s.kind == "cli_default" and s.name == "--label-smoothing" for f in built.files
               for s in f.symbols)  # fmt: skip


def test_library_calls_are_qualified() -> None:
    built = build_index(FIXTURE, REPO, SHA)
    attention = next(f for f in built.files if f.path == "toyformer/attention.py")
    calls = {q for _, q in library_calls(attention, set(built.index.library_modules))}
    assert "torch.nn.functional.softmax" in calls  # F.softmax via `import ... as F`
    assert "torch.matmul" in calls
    assert "torch.nn.Linear" in calls  # nn.Linear via `from torch import nn`


QUERIES = [
    ("scaled dot product attention", "toyformer/attention.py::scaled_dot_product_attention"),
    ("multi-head attention heads", "toyformer/attention.py::MultiHeadAttention"),
    ("positional encoding sine cosine", "toyformer/layers.py::PositionalEncoding"),
    ("label smoothing", "toyformer/train.py::LabelSmoothingLoss"),
    ("learning rate warmup", "toyformer/train.py::rate"),
    ("residual connection layer normalization", "toyformer/layers.py::SublayerConnection"),
    ("position-wise feed-forward network", "toyformer/layers.py::PositionwiseFeedForward"),
]


@pytest.mark.parametrize(("query", "expected"), QUERIES)
def test_known_concepts_in_top_3_bm25(store: IndexStore, query: str, expected: str) -> None:
    hits = retrieve(store, REPO, SHA, query, k=3)
    assert expected in [h.symbol.id for h in hits], [h.symbol.id for h in hits]


@pytest.mark.parametrize(("query", "expected"), QUERIES)
def test_known_concepts_in_top_3_hybrid(store: IndexStore, query: str, expected: str) -> None:
    embedder = HashEmbedder()
    embed_symbols(store, REPO, SHA, embedder)
    hits = retrieve(store, REPO, SHA, query, k=3, embedder=embedder)
    assert expected in [h.symbol.id for h in hits], [h.symbol.id for h in hits]


def test_config_hyperparameter_is_retrievable(store: IndexStore) -> None:
    hits = retrieve(store, REPO, SHA, "label_smoothing", k=5)
    assert "configs/base.yaml::train.label_smoothing" in [h.symbol.id for h in hits]


def test_mid_size_repo_indexes_fast(tmp_path: Path, db: Database) -> None:
    """Spec M3: ~200 Python files index in < 60 s on a laptop (we hold ourselves to 20 s)."""
    root = tmp_path / "big"
    template = (FIXTURE / "toyformer" / "layers.py").read_text()
    for i in range(200):
        pkg = root / f"pkg{i // 20}"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / f"mod{i}.py").write_text(template.replace("class ", f"class M{i}_"))
    start = time.perf_counter()
    built = index_checkout(IndexStore(db), root, "local:big", "content:big")
    elapsed = time.perf_counter() - start
    assert len(built.index.files) == 200
    assert elapsed < 20, f"indexing took {elapsed:.1f}s"
