"""Python / notebook indexer (spec §10.4)."""

from __future__ import annotations

import hashlib
import json
import time
from textwrap import dedent

from portolan.code.python_index import index_notebook, index_python
from portolan.models.code import FileIndex, Symbol


def _src(text: str) -> bytes:
    return dedent(text).lstrip("\n").encode("utf-8")


def _by_qualname(index: FileIndex) -> dict[str, Symbol]:
    return {s.qualname: s for s in index.symbols}


DEFS = _src(
    '''
    import functools


    class Net(Base):
        """A network."""

        def __init__(self, d):
            self.d = d

        @functools.cache
        @staticmethod
        def helper(x):
            def inner(y):
                return y
            return inner(x)

        async def aforward(self, x):
            return x


    @register("net")
    def build():
        return Net(3)
    '''
)


def test_classes_methods_nested_async_and_decorators() -> None:
    idx = index_python("model/net.py", DEFS)
    assert idx.language == "python"
    assert not idx.parse_errors
    syms = _by_qualname(idx)

    module = syms[""]
    assert (module.kind, module.start_line, module.end_line) == ("module", 1, idx.n_lines)
    assert module.id == "model/net.py::"

    net = syms["Net"]
    assert (net.kind, net.start_line, net.end_line, net.parent) == ("class", 4, 18, None)
    assert net.signature == "class Net(Base):"
    assert net.id == "model/net.py::Net"

    init = syms["Net.__init__"]
    assert (init.kind, init.parent, init.start_line) == ("method", "Net", 7)
    assert init.signature == "def __init__(self, d):"

    helper = syms["Net.helper"]
    assert helper.kind == "method"
    assert (helper.start_line, helper.end_line) == (10, 15)  # span starts at the first decorator
    assert helper.signature == "def helper(x):"

    inner = syms["Net.helper.inner"]
    assert (inner.kind, inner.parent, inner.start_line) == ("function", "Net.helper", 13)

    aforward = syms["Net.aforward"]
    assert aforward.kind == "method"
    assert aforward.signature == "async def aforward(self, x):"

    build = syms["build"]
    assert (build.kind, build.start_line, build.end_line, build.parent) == (
        "function",
        21,
        23,
        None,
    )


DOCS = _src(
    '''
    # leading comment
    """Module doc."""


    class A:
        \'\'\'Single-quoted class doc.\'\'\'

        def f(self):
            """Summary line.

            Details, indented
                more indented.
            """

        def g(self):
            "plain quotes"

        def h(self):
            x = 1
            """not a docstring"""


    def one(): """inline"""
    '''
)


def test_docstrings() -> None:
    syms = _by_qualname(index_python("docs.py", DOCS))
    assert syms[""].docstring == "Module doc."
    assert syms["A"].docstring == "Single-quoted class doc."
    assert syms["A.f"].docstring == "Summary line.\n\nDetails, indented\n    more indented."
    assert syms["A.g"].docstring == "plain quotes"
    assert syms["A.h"].docstring is None
    assert syms["one"].docstring == "inline"


def test_no_module_docstring() -> None:
    syms = _by_qualname(index_python("m.py", b"x = f'{y}'\n"))
    assert syms[""].docstring is None


CONSTANTS = _src(
    """
    D_MODEL = 512
    dropout: float = 0.1
    H, W = 8, 64
    a = b = "same"
    annotated_only: int
    SCHEDULE = {"warmup": 4000}
    LONG = "%s"
    obj.attr = 3


    class Config:
        n_layers: int = 6

        def method(self):
            local = 1


    if __name__ == "__main__":
        hidden = 2
    """
    % ("x" * 300)
)


def test_constants() -> None:
    idx = index_python("cfg.py", CONSTANTS)
    consts = {s.qualname: s for s in idx.symbols if s.kind == "constant"}
    assert consts["D_MODEL"].value == "512"
    assert consts["D_MODEL"].start_line == 1
    assert consts["dropout"].value == "0.1"
    assert consts["H"].value == consts["W"].value == "8, 64"
    assert consts["a"].value == consts["b"].value == '"same"'
    assert consts["SCHEDULE"].value == '{"warmup": 4000}'
    long_value = consts["LONG"].value
    assert long_value is not None
    assert len(long_value) == 200
    cls_const = consts["Config.n_layers"]
    assert (cls_const.name, cls_const.value, cls_const.parent) == ("n_layers", "6", "Config")
    for missing in ("annotated_only", "local", "Config.method.local", "hidden", "obj.attr"):
        assert missing not in consts


CLI = _src(
    """
    import argparse

    import click


    def get_args():
        p = argparse.ArgumentParser()
        p.add_argument("--lr", type=float, default=1e-3)
        p.add_argument("-b", "--batch-size", default=32, type=int)
        p.add_argument("--no-default", action="store_true")
        return p.parse_args()


    @click.command()
    @click.option("--epochs", default=10)
    def main(epochs):
        pass
    """
)


def test_cli_defaults() -> None:
    idx = index_python("train.py", CLI)
    cli = {s.qualname: s for s in idx.symbols if s.kind == "cli_default"}
    assert set(cli) == {"argparse:--lr", "argparse:--batch-size", "click:--epochs"}
    lr = cli["argparse:--lr"]
    assert (lr.name, lr.value, lr.start_line, lr.parent) == ("--lr", "1e-3", 8, "get_args")
    assert lr.id == "train.py::argparse:--lr"
    assert cli["argparse:--batch-size"].value == "32"
    assert cli["click:--epochs"].value == "10"


IMPORTS = _src(
    """
    import torch
    import torch.nn.functional as F, numpy as np
    from torch import nn, optim
    from x.y import z as w, v
    from .layers import Attn
    from .. import utils
    from ...pkg.mod import *


    def lazy():
        import json
    """
)


def test_imports() -> None:
    imps = [(i.line, i.module, i.names, i.alias) for i in index_python("p/m.py", IMPORTS).imports]
    assert imps == [
        (1, "torch", [], None),
        (2, "torch.nn.functional", [], "F"),
        (2, "numpy", [], "np"),
        (3, "torch", ["nn", "optim"], None),
        (4, "x.y", ["v"], None),
        (4, "x.y", ["z"], "w"),
        (5, ".layers", ["Attn"], None),
        (6, "..", ["utils"], None),
        (7, "...pkg.mod", ["*"], None),
        (11, "json", [], None),
    ]


CALLS = _src(
    """
    import torch
    import torch.nn.functional as F
    from torch import nn

    LAYER = nn.Linear(4, 4)


    class Attn(nn.Module):
        def forward(self, x, mask=make_mask()):
            scores = torch.softmax(self.attn(x), dim=-1)
            out = F.dropout(scores).sum()
            return get_fn()(out), xs[0](), super().forward(x)
    """
)


def test_calls_with_in_symbol() -> None:
    calls = [(c.line, c.callee, c.in_symbol) for c in index_python("a.py", CALLS).calls]
    assert calls == [
        (5, "nn.Linear", None),
        (9, "make_mask", "Attn"),
        (10, "torch.softmax", "Attn.forward"),
        (10, "self.attn", "Attn.forward"),
        (11, "F.dropout", "Attn.forward"),
        (12, "get_fn", "Attn.forward"),
        (12, "super", "Attn.forward"),
    ]


def test_syntax_error_keeps_partial_results() -> None:
    src = _src(
        """
        def good():
            return 1

        def broken(:
            pass

        class After:
            def m(self):
                return 2
        """
    )
    idx = index_python("bad.py", src)
    assert idx.parse_errors
    syms = _by_qualname(idx)
    assert syms["good"].kind == "function"
    assert "After" in syms


def test_notebook() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n", "def not_code(): pass"]},
            {
                "cell_type": "code",
                "source": ["%matplotlib inline\n", "import torch\n", "!pip install x\n", "D = 4"],
                "outputs": [{"text": "def from_output(): pass"}],
            },
            {"cell_type": "code", "source": "%%bash\necho hi"},
            {"cell_type": "code", "source": "def run():\n    return torch.zeros(D)"},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    raw = json.dumps(nb).encode("utf-8")
    idx = index_notebook("nb/demo.ipynb", raw)
    assert idx.language == "notebook"
    assert idx.sha256 == hashlib.sha256(raw).hexdigest()
    assert idx.virtual_text == (
        "%matplotlib inline\nimport torch\n!pip install x\nD = 4\n"
        "%%bash\necho hi\n"
        "def run():\n    return torch.zeros(D)"
    )
    assert idx.n_lines == 8
    assert not idx.parse_errors
    syms = _by_qualname(idx)
    assert "not_code" not in syms
    assert "from_output" not in syms
    assert syms["D"].value == "4"
    assert syms["D"].start_line == 4
    assert syms["run"].start_line == 7
    assert [(i.line, i.module) for i in idx.imports] == [(2, "torch")]
    assert [(c.line, c.callee, c.in_symbol) for c in idx.calls] == [(8, "torch.zeros", "run")]


def test_notebook_invalid_json() -> None:
    idx = index_notebook("x.ipynb", b"{not json")
    assert idx.parse_errors
    assert idx.symbols == []


def test_non_ascii() -> None:
    src = _src(
        '''
        """Modul für Aufmerksamkeit — QKᵀ/√dₖ."""

        GRÖSSE = "größe ✓"


        def berechne_λ(x):
            return softmax_ψ(x)
        '''
    )
    idx = index_python("ü.py", src)
    syms = _by_qualname(idx)
    assert syms[""].docstring == "Modul für Aufmerksamkeit — QKᵀ/√dₖ."
    assert syms["GRÖSSE"].value == '"größe ✓"'
    assert syms["berechne_λ"].start_line == 6
    assert syms["berechne_λ"].signature == "def berechne_λ(x):"
    assert [(c.line, c.callee) for c in idx.calls] == [(7, "softmax_ψ")]


def test_invalid_utf8_is_replaced() -> None:
    idx = index_python("bin.py", b'X = "\xff"\n')
    assert idx.n_lines == 1
    assert _by_qualname(idx)["X"].value == '"�"'


def test_duplicate_qualnames() -> None:
    src = _src(
        """
        def f():
            pass

        def f():
            pass

        if True:
            def f():
                pass
        """
    )
    ids = [s.id for s in index_python("d.py", src).symbols if s.qualname == "f"]
    assert ids == ["d.py::f", "d.py::f#2", "d.py::f#3"]


def test_sha_and_line_count() -> None:
    src = b"a = 1\nb = 2\n"
    idx = index_python("s.py", src)
    assert idx.sha256 == hashlib.sha256(src).hexdigest()
    assert idx.n_lines == 2
    assert index_python("empty.py", b"").n_lines == 0


def test_perf_2000_lines() -> None:
    block = dedent(
        '''
        class Block{i}(nn.Module):
            """Block {i}."""

            SIZE = {i}

            def forward(self, x):
                y = torch.relu(self.lin(x))
                return F.dropout(y, p=0.1)

        '''
    )
    src = "import torch\n" + "".join(block.format(i=i) for i in range(200))
    assert src.count("\n") >= 2000
    start = time.perf_counter()
    idx = index_python("big.py", src.encode("utf-8"))
    elapsed = time.perf_counter() - start
    assert len([s for s in idx.symbols if s.kind == "class"]) == 200
    assert elapsed < 1.0, f"indexing took {elapsed:.2f}s"
