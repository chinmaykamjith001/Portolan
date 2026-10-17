from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from portolan.code.index_store import IndexStore, identifier_words, split_lines
from portolan.models.code import CallRef, CodeIndex, FileIndex, ImportRef, Symbol, SymbolKind
from portolan.store.db import Database, open_database

REPO = "github:example/transformer"
SHA = "a" * 40


@dataclass
class Def:
    """One symbol to lay out in a synthetic source file."""

    name: str
    kind: SymbolKind = "function"
    doc: str | None = None
    body: list[str] = field(default_factory=lambda: ["pass"])
    parent: str | None = None


def build_file(path: str, defs: list[Def]) -> tuple[FileIndex, str]:
    """Write each Def as `def name(...):` + docstring + body and return matching line spans."""
    lines: list[str] = []
    symbols: list[Symbol] = []
    for d in defs:
        start = len(lines) + 1
        indent = "    " if d.parent else ""
        keyword = "class" if d.kind == "class" else "def"
        signature = f"{indent}{keyword} {d.name}(self):" if d.parent else f"{keyword} {d.name}():"
        lines.append(signature)
        if d.doc:
            lines.append(f'{indent}    """{d.doc}"""')
        lines.extend(f"{indent}    {b}" for b in d.body)
        qualname = f"{d.parent}.{d.name}" if d.parent else d.name
        symbols.append(
            Symbol(
                id=f"{path}::{qualname}",
                kind=d.kind,
                name=d.name,
                qualname=qualname,
                path=path,
                start_line=start,
                end_line=len(lines),
                docstring=d.doc,
                signature=signature.strip(),
                parent=d.parent,
            )
        )
        lines.append("")
    text = "\n".join(lines) + "\n"
    file = FileIndex(
        path=path,
        language="python",
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        n_lines=len(split_lines(text)),
        symbols=symbols,
    )
    return file, text


def save_files(
    store: IndexStore, files: list[tuple[FileIndex, str]], *, repo: str = REPO, sha: str = SHA
) -> CodeIndex:
    index = CodeIndex(
        repo_id=repo,
        commit_sha=sha,
        root="/tmp/checkout",
        files=[f.path for f, _ in files],
        symbol_count=sum(len(f.symbols) for f, _ in files),
        library_modules=["torch"],
        skipped={"data/big.bin": "binary"},
    )
    store.save(index, [f for f, _ in files], {f.path: t for f, t in files})
    return index


def attention_files() -> list[tuple[FileIndex, str]]:
    model, model_text = build_file(
        "model/attention.py",
        [
            Def("MultiHeadAttention", kind="class", doc="Multi-head attention block."),
            Def(
                "forward",
                kind="method",
                parent="MultiHeadAttention",
                doc="Project, attend, concatenate.",
                body=["q = self.w_q(x)", "return softmax(q @ k.T / sqrt(d_k)) @ v"],
            ),
            Def("label_smoothing", doc="Spread eps over the vocabulary.", body=["return 0.1"]),
        ],
    )
    model.imports = [ImportRef(path="model/attention.py", line=1, module="torch", alias=None)]
    model.calls = [
        CallRef(path="model/attention.py", line=4, callee="softmax", in_symbol="forward")
    ]
    train, train_text = build_file("train.py", [Def("train_step", body=["loss.backward()"])])
    return [(model, model_text), (train, train_text)]


def test_identifier_words() -> None:
    assert identifier_words("MultiHeadAttention") == ["multi", "head", "attention"]
    assert identifier_words("d_model") == ["d", "model"]
    assert identifier_words("--label-smoothing") == ["label", "smoothing"]
    assert identifier_words("HTTPServer2") == ["http", "server", "2"]
    assert identifier_words("model.d_model") == ["model", "d", "model"]


def test_save_load_round_trip(db: Database) -> None:
    store = IndexStore(db)
    assert not store.has_index(REPO, SHA)
    assert store.load_index(REPO, SHA) is None

    files = attention_files()
    index = save_files(store, files)

    assert store.has_index(REPO, SHA)
    assert store.load_index(REPO, SHA) == index
    expected = sorted(
        (s for f, _ in files for s in f.symbols), key=lambda s: (s.path, s.start_line)
    )
    assert store.symbols(REPO, SHA) == expected
    assert [s.name for s in store.symbols(REPO, SHA, path="train.py")] == ["train_step"]
    assert [s.name for s in store.symbols(REPO, SHA, kinds={"method"})] == ["forward"]
    assert store.symbols(REPO, SHA, kinds=set()) == []

    forward = store.symbol(REPO, SHA, "model/attention.py::MultiHeadAttention.forward")
    assert forward is not None
    assert forward.parent == "MultiHeadAttention"
    assert forward.docstring == "Project, attend, concatenate."
    assert store.symbol(REPO, SHA, "nope") is None
    assert store.symbol(REPO, "b" * 40, forward.id) is None

    assert store.file_text(REPO, SHA, "train.py") == files[1][1]
    assert store.file_text(REPO, SHA, "missing.py") is None
    assert store.imports(REPO, SHA) == files[0][0].imports
    assert store.calls(REPO, SHA, "model/attention.py") == files[0][0].calls


def test_resave_replaces_everything(db: Database) -> None:
    store = IndexStore(db)
    save_files(store, attention_files())
    other_sha = "c" * 40
    save_files(store, attention_files(), sha=other_sha)  # another commit is left alone

    new_file, new_text = build_file("model/ffn.py", [Def("PositionwiseFeedForward", kind="class")])
    save_files(store, [(new_file, new_text)])

    assert [s.name for s in store.symbols(REPO, SHA)] == ["PositionwiseFeedForward"]
    assert store.file_text(REPO, SHA, "train.py") is None
    assert store.imports(REPO, SHA) == []
    assert store.calls(REPO, SHA) == []
    fts = db.fetchall("SELECT name FROM code_fts WHERE repo_key = ?", (f"{REPO}@{SHA}",))
    assert [r["name"] for r in fts] == ["PositionwiseFeedForward"]
    assert len(store.symbols(REPO, other_sha)) == 4

    store.delete(REPO, SHA)
    assert not store.has_index(REPO, SHA)
    assert store.symbols(REPO, SHA) == []
    assert db.fetchall("SELECT 1 FROM code_fts WHERE repo_key = ?", (f"{REPO}@{SHA}",)) == []
    assert store.has_index(REPO, other_sha)


def test_fts_columns(db: Database) -> None:
    store = IndexStore(db)
    save_files(store, attention_files())
    row = db.fetchone(
        "SELECT name_parts, body FROM code_fts WHERE symbol_id = ?",
        ("model/attention.py::MultiHeadAttention.forward",),
    )
    assert row is not None
    assert row["name_parts"] == "forward"
    assert row["body"].startswith("    def forward(self):")
    assert "softmax(q @ k.T" in row["body"]
    row = db.fetchone("SELECT name_parts FROM code_fts WHERE name = 'MultiHeadAttention'")
    assert row is not None
    assert row["name_parts"] == "multi head attention"


def test_body_is_capped(db: Database) -> None:
    store = IndexStore(db)
    big, text = build_file("big.py", [Def("huge", body=[f"x{i} = {i}" for i in range(2_000)])])
    save_files(store, [(big, text)])
    row = db.fetchone("SELECT body FROM code_fts WHERE name = 'huge'")
    assert row is not None
    assert len(row["body"]) <= 4_000


def test_notebook_text_falls_back_to_virtual_text(db: Database) -> None:
    store = IndexStore(db)
    nb = FileIndex(
        path="demo.ipynb",
        language="notebook",
        sha256="0" * 64,
        n_lines=2,
        virtual_text="import torch\nx = torch.ones(3)",
    )
    index = CodeIndex(
        repo_id=REPO, commit_sha=SHA, root="/r", files=["demo.ipynb"], symbol_count=0,
        library_modules=[],
    )  # fmt: skip
    store.save(index, [nb], {})
    assert store.lines(REPO, SHA, "demo.ipynb", 2, 2) == ["x = torch.ones(3)"]


def test_line_slicing(db: Database) -> None:
    store = IndexStore(db)
    text = "one\ntwo\r\nthree\rfour\n"
    f = FileIndex(path="a.py", language="python", sha256="0" * 64, n_lines=4)
    index = CodeIndex(
        repo_id=REPO, commit_sha=SHA, root="/r", files=["a.py"], symbol_count=0,
        library_modules=[],
    )  # fmt: skip
    store.save(index, [f], {"a.py": text})

    assert store.lines(REPO, SHA, "a.py", 1, 4) == ["one", "two", "three", "four"]
    assert store.lines(REPO, SHA, "a.py", 2, 3) == ["two", "three"]
    assert store.lines(REPO, SHA, "a.py", 3, 3) == ["three"]
    assert store.lines(REPO, SHA, "a.py", 0, 2) == ["one", "two"]  # clamped at the start
    assert store.lines(REPO, SHA, "a.py", 3, 99) == ["three", "four"]  # … and at the end
    assert store.lines(REPO, SHA, "a.py", 5, 9) == []
    assert store.lines(REPO, SHA, "a.py", 3, 2) == []
    assert store.lines(REPO, SHA, "missing.py", 1, 2) == []


def test_split_lines_keeps_form_feeds() -> None:
    # str.splitlines() would also break on \x0c and shift every later line number.
    assert split_lines("a\x0cb\nc\n") == ["a\x0cb", "c"]
    assert split_lines("") == []
    assert split_lines("x") == ["x"]


def test_fresh_database_migrates_to_version_4(tmp_path: Path) -> None:
    db = open_database(tmp_path / "fresh.db")
    try:
        assert db.schema_version() >= 4
        tables = {r["name"] for r in db.fetchall("SELECT name FROM sqlite_master")}
        assert {
            "code_indexes",
            "code_files",
            "code_symbols",
            "code_imports",
            "code_calls",
            "code_fts",
            "code_embeddings",
        } <= tables
    finally:
        db.close()
