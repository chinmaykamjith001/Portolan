from __future__ import annotations

import hashlib

import pytest

from portolan.code.config_index import MAX_SYMBOLS, index_config
from portolan.models.code import FileIndex, Symbol


def by_q(idx: FileIndex) -> dict[str, Symbol]:
    return {s.qualname: s for s in idx.symbols}


def test_unsupported_extension() -> None:
    with pytest.raises(ValueError):
        index_config("a.ini", b"x=1")


def test_yaml_nested_and_exact_lines() -> None:
    src = (
        b"# training config\n"
        b"model:\n"
        b"  layers:\n"
        b"    - dim: 512\n"
        b"      act: relu  # comment\n"
        b"    - dim: 256\n"
        b"  d_model: 512\n"
        b"loss:\n"
        b"  label_smoothing: 0.0\n"
        b"  lr: 1e-3\n"
        b"  name: 'adam'\n"
        b"  flag: true\n"
        b"  steps: [1, 2]\n"
    )
    idx = index_config("cfg/train.yaml", src)
    q = by_q(idx)
    assert idx.language == "yaml" and not idx.parse_errors
    assert idx.sha256 == hashlib.sha256(src).hexdigest()
    assert idx.n_lines == 13
    ls = q["loss.label_smoothing"]
    assert (ls.start_line, ls.end_line, ls.value, ls.name) == (9, 9, "0.0", "label_smoothing")
    assert ls.id == "cfg/train.yaml::loss.label_smoothing"
    assert ls.kind == "config_key" and ls.parent == "loss"
    assert q["loss.lr"].value == "1e-3"
    assert q["loss.name"].value == "'adam'"
    assert q["loss.flag"].value == "true"
    assert q["model.layers[0].dim"].value == "512"
    assert q["model.layers[1].dim"].start_line == 6
    assert q["model.layers[0].act"].value == "relu"
    assert q["loss.steps[1]"].value == "2"
    assert q["loss.steps[1]"].name == "[1]"
    assert q["model"].value is None and q["model"].start_line == 2 and q["model"].end_line == 7
    assert q["model.layers"].value is None
    assert q["model.layers[0]"].value is None


def test_yaml_anchors_aliases() -> None:
    src = b"base: &b\n  lr: 0.1\nrun:\n  <<: *b\n  alias: *b\n  tagged: &t 5\n"
    q = by_q(index_config("a.yml", src))
    assert q["base.lr"].value == "0.1"
    assert q["run.alias"].value == "*b"
    assert q["run.<<"].value == "*b"
    assert q["run.tagged"].value == "5"


def test_yaml_multi_doc() -> None:
    src = b"a: 1\n---\nb: 2\n---\nc:\n  d: 3\n"
    q = by_q(index_config("m.yaml", src))
    assert q["a"].value == "1"
    assert q["doc1.b"].value == "2"
    assert q["doc2.c.d"].value == "3"
    assert q["doc1"].value is None


def test_yaml_dotted_key_quoted() -> None:
    q = by_q(index_config("a.yaml", b"'a.b': 1\n"))
    assert q['["a.b"]'].value == "1"
    assert q['["a.b"]'].name == "a.b"


def test_yaml_empty_value_and_block_scalar() -> None:
    q = by_q(index_config("a.yaml", b"x:\ny: |\n  line1\n  line2\nz: {}\n"))
    assert q["x"].value == ""
    assert q["y"].start_line == 2 and q["y"].end_line == 4
    assert q["y"].value is not None and q["y"].value.startswith("|")
    assert q["z"].value == "{}"


def test_json_arrays_of_objects() -> None:
    src = (
        b'{\n "model": {"d": 512, "n.m": true},\n "runs": [\n  {"lr": 0.1},\n'
        b'  {"lr": 1e-3, "tag": "x"}\n ],\n "e": null\n}\n'
    )
    idx = index_config("c.json", src)
    q = by_q(idx)
    assert not idx.parse_errors and idx.language == "json"
    assert q["model.d"].value == "512" and q["model.d"].start_line == 2
    assert q['model["n.m"]'].value == "true"
    assert q["runs[0].lr"].value == "0.1"
    assert q["runs[1].lr"].value == "1e-3" and q["runs[1].lr"].start_line == 5
    assert q["runs[1].tag"].value == '"x"'
    assert q["e"].value == "null"
    assert q["runs"].value is None and q["runs"].start_line == 3 and q["runs"].end_line == 6
    assert q["model"].value is None


def test_json_top_level_array() -> None:
    q = by_q(index_config("a.json", b'[{"a": 1}]'))
    assert q["[0].a"].value == "1"


def test_toml_tables_and_arrays() -> None:
    src = (
        b"title = 'x'  # c\n"
        b"a.b = 1\n"
        b"[model]\n"
        b"d_model = 512\n"
        b"opt = {lr = 0.1, betas = [0.9, 0.98]}\n"
        b"[model.deep]\n"
        b'"k.k" = true\n'
        b"[[runs]]\n"
        b"lr = 0.1\n"
        b"[[runs]]\n"
        b"lr = 0.2\n"
        b"[[runs.steps]]\n"
        b"n = 3\n"
    )
    idx = index_config("p.toml", src)
    q = by_q(idx)
    assert not idx.parse_errors and idx.language == "toml"
    assert q["title"].value == "'x'"
    assert q["a.b"].value == "1" and q["a"].value is None
    assert q["model.d_model"].value == "512" and q["model.d_model"].start_line == 4
    assert q["model.opt.lr"].value == "0.1"
    assert q["model.opt.betas[1]"].value == "0.98"
    assert q["model.opt"].value is None
    assert q["model"].value is None and q["model.deep"].value is None
    assert q['model.deep["k.k"]'].value == "true"
    assert q["runs[0].lr"].value == "0.1"
    assert q["runs[1].lr"].value == "0.2" and q["runs[1].lr"].start_line == 11
    assert q["runs[1].steps[0].n"].value == "3"


def test_broken_files_flag_parse_errors() -> None:
    idx = index_config("b.yaml", b"a: 1\nb: [1, 2\nc: : :\n")
    assert idx.parse_errors
    j = index_config("b.json", b'{"a": 1, "b": ')
    assert j.parse_errors
    assert by_q(j)["a"].value == "1"
    assert index_config("b.toml", b"a = 1\nb = = 2\n").parse_errors


def test_symbol_cap() -> None:
    n = MAX_SYMBOLS + 500
    src = ("\n".join(f"k{i} = {i}" for i in range(n)) + "\n").encode()
    idx = index_config("big.toml", src)
    assert len(idx.symbols) == MAX_SYMBOLS
    assert not idx.parse_errors
    big = ("[" + ",".join(str(i) for i in range(n)) + "]").encode()
    assert len(index_config("big.json", big).symbols) == MAX_SYMBOLS


def test_value_truncated_and_duplicate_ids() -> None:
    long = b'{"a": "' + b"x" * 500 + b'", "a": 2}'
    idx = index_config("d.json", long)
    assert idx.symbols[0].value is not None and len(idx.symbols[0].value) == 200
    ids = [s.id for s in idx.symbols]
    assert len(set(ids)) == len(ids)
    assert "d.json::a#2" in ids


def test_non_utf8_and_empty() -> None:
    idx = index_config("x.yaml", b"a: caf\xe9\n")
    assert idx.symbols[0].value == "caf\ufffd"
    empty = index_config("e.yaml", b"")
    assert empty.n_lines == 0 and empty.symbols == []
