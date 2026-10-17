"""Index YAML / JSON / TOML config files as key → value symbols (spec §10.4).

Every leaf key becomes a ``config_key`` Symbol with its verbatim source text as ``value``; every
mapping/sequence that holds nested entries becomes a ``config_key`` Symbol with ``value=None``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

import tree_sitter_json
import tree_sitter_toml
import tree_sitter_yaml
from tree_sitter import Language, Node, Parser

from portolan.models.code import FileIndex, Symbol

# Huge generated configs (embedding tables, lock-style dumps) would otherwise produce
# hundreds of thousands of symbols. Past this many symbols the rest of the file is dropped;
# the symbols already collected are kept and parse_errors is NOT set (truncation is not an error).
MAX_SYMBOLS = 5_000
MAX_VALUE_CHARS = 200

_Part = str | int
_Path = tuple[_Part, ...]
_Lang = Literal["yaml", "json", "toml"]

_EXT: dict[str, _Lang] = {".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml"}


def _fmt_path(path: _Path) -> str:
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        elif not part or any(c in part for c in '.[]"'):
            out += '["' + part.replace("\\", "\\\\").replace('"', '\\"') + '"]'
        else:
            out += f".{part}" if out else part
    return out


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _last_line(node: Node) -> int:
    """1-based last line of a node; a node ending at column 0 stops on the previous line."""
    row, col = node.end_point
    if col == 0 and row > node.start_point[0]:
        return row
    return row + 1


@dataclass
class _Emitter:
    file_path: str
    symbols: list[Symbol] = field(default_factory=list[Symbol])
    _ids: dict[str, int] = field(default_factory=dict[str, int])
    _containers: set[_Path] = field(default_factory=set[_Path])

    @property
    def full(self) -> bool:
        return len(self.symbols) >= MAX_SYMBOLS

    def _add(self, path: _Path, span: Node, value: str | None) -> None:
        if self.full or not path:
            return
        qualname = _fmt_path(path)
        sym_id = f"{self.file_path}::{qualname}"
        n = self._ids.get(sym_id, 0) + 1
        self._ids[sym_id] = n
        if n > 1:
            sym_id = f"{sym_id}#{n}"
        last = path[-1]
        self.symbols.append(
            Symbol(
                id=sym_id,
                kind="config_key",
                name=f"[{last}]" if isinstance(last, int) else last,
                qualname=qualname,
                path=self.file_path,
                start_line=span.start_point[0] + 1,
                end_line=_last_line(span),
                value=value,
                parent=_fmt_path(path[:-1]) or None,
            )
        )

    def leaf(self, path: _Path, span: Node, value: str) -> None:
        self._add(path, span, value[:MAX_VALUE_CHARS])

    def container(self, path: _Path, span: Node) -> None:
        self._containers.add(path)
        self._add(path, span, None)

    def ensure_container(self, path: _Path, span: Node) -> None:
        if path not in self._containers:
            self.container(path, span)


# ---------------------------------------------------------------- YAML

_YAML_SKIP = {"anchor", "tag", "comment", "-", "?", ":"}
_YAML_MAP = {"block_mapping", "flow_mapping"}
_YAML_SEQ = {"block_sequence", "flow_sequence"}


def _y_unwrap(node: Node | None) -> Node | None:
    """Strip flow_node/block_node wrappers, anchors and tags down to the content node."""
    while node is not None and node.type in ("flow_node", "block_node"):
        inner = [c for c in node.named_children if c.type not in _YAML_SKIP]
        node = inner[0] if inner else None
    return node


def _y_key(node: Node) -> str:
    content = _y_unwrap(node)
    if content is None:
        return ""
    text = _text(content)
    if content.type in ("single_quote_scalar", "double_quote_scalar") and len(text) >= 2:
        return text[1:-1]
    return text


def _y_pair(em: _Emitter, base: _Path, pair: Node) -> None:
    key: Node | None = None
    value: Node | None = None
    seen_colon = False
    for c in pair.children:
        if c.type == ":":
            seen_colon = True
        elif c.type in ("flow_node", "block_node"):
            if seen_colon:
                value = c
                break
            if key is None:
                key = c
    if key is None:
        return
    _y_value(em, (*base, _y_key(key)), value, pair)


def _y_value(em: _Emitter, path: _Path, node: Node | None, span: Node) -> None:
    if em.full:
        return
    content = _y_unwrap(node)
    if content is None:
        em.leaf(path, span, "")
    elif content.type in _YAML_MAP:
        pairs = [c for c in content.named_children if c.type in ("block_mapping_pair", "flow_pair")]
        if not pairs:
            em.leaf(path, span, _text(content))
            return
        em.container(path, span)
        for p in pairs:
            if em.full:
                return
            _y_pair(em, path, p)
    elif content.type in _YAML_SEQ:
        items = [
            c
            for c in content.named_children
            if c.type in ("block_sequence_item", "flow_node", "flow_pair")
        ]
        if not items:
            em.leaf(path, span, _text(content))
            return
        em.container(path, span)
        for i, item in enumerate(items):
            if em.full:
                return
            item_path = (*path, i)
            if item.type == "flow_pair":
                em.container(item_path, item)
                _y_pair(em, item_path, item)
            elif item.type == "block_sequence_item":
                inner = [c for c in item.named_children if c.type != "comment"]
                _y_value(em, item_path, inner[0] if inner else None, item)
            else:
                _y_value(em, item_path, item, item)
    else:
        em.leaf(path, span, _text(content))


def _walk_yaml(em: _Emitter, root: Node) -> None:
    # A syntax error can make the whole stream an ERROR node; keep any pairs that parsed.
    for pair in root.named_children if root.type == "ERROR" else []:
        if pair.type == "block_mapping_pair":
            _y_pair(em, (), pair)
    docs = [c for c in root.named_children if c.type == "document"]
    for i, doc in enumerate(docs):
        body = [c for c in doc.named_children if c.type in ("block_node", "flow_node")]
        base: _Path = (f"doc{i}",) if i > 0 else ()
        _y_value(em, base, body[0] if body else None, doc)


# ---------------------------------------------------------------- JSON


def _walk_json(em: _Emitter, root: Node) -> None:
    for child in root.named_children:
        if child.type == "ERROR":  # keep any pairs that parsed before the syntax error
            for p in child.named_children:
                if p.type == "pair":
                    _j_pair(em, (), p)
        elif child.type != "comment":
            _j_value(em, (), child, child)
            return


def _j_pair(em: _Emitter, base: _Path, pair: Node) -> None:
    key = pair.child_by_field_name("key")
    val = pair.child_by_field_name("value")
    if key is None or val is None:
        return
    text = _text(key)
    _j_value(em, (*base, text[1:-1] if len(text) >= 2 else text), val, pair)


def _j_value(em: _Emitter, path: _Path, node: Node, span: Node) -> None:
    if em.full:
        return
    if node.type == "object":
        pairs = [c for c in node.named_children if c.type == "pair"]
        if not pairs:
            em.leaf(path, span, _text(node))
            return
        em.container(path, span)
        for p in pairs:
            _j_pair(em, path, p)
            if em.full:
                return
    elif node.type == "array":
        items = [c for c in node.named_children if c.type != "comment"]
        if not items:
            em.leaf(path, span, _text(node))
            return
        em.container(path, span)
        for i, item in enumerate(items):
            _j_value(em, (*path, i), item, item)
            if em.full:
                return
    else:
        em.leaf(path, span, _text(node))


# ---------------------------------------------------------------- TOML

_TOML_KEYS = {"bare_key", "quoted_key", "dotted_key"}


def _t_key_parts(key: Node) -> list[str]:
    if key.type == "dotted_key":
        return [p for c in key.named_children if c.type in _TOML_KEYS for p in _t_key_parts(c)]
    text = _text(key)
    if key.type == "quoted_key" and len(text) >= 2:
        return [text[1:-1]]
    return [text]


def _t_value(em: _Emitter, path: _Path, node: Node, span: Node) -> None:
    if em.full:
        return
    if node.type == "inline_table":
        pairs = [c for c in node.named_children if c.type == "pair"]
        if not pairs:
            em.leaf(path, span, _text(node))
            return
        em.container(path, span)
        for p in pairs:
            _t_pair(em, path, p)
            if em.full:
                return
    elif node.type == "array":
        items = [c for c in node.named_children if c.type != "comment"]
        if not items:
            em.leaf(path, span, _text(node))
            return
        em.container(path, span)
        for i, item in enumerate(items):
            _t_value(em, (*path, i), item, item)
            if em.full:
                return
    else:
        em.leaf(path, span, _text(node))


def _t_pair(em: _Emitter, base: _Path, pair: Node) -> None:
    named = [c for c in pair.named_children if c.type != "comment"]
    if len(named) < 2 or named[0].type not in _TOML_KEYS:
        return
    parts = _t_key_parts(named[0])
    path: _Path = (*base, *parts)
    for n in range(len(base) + 1, len(path)):  # dotted key: a.b = 1 implies mapping `a`
        em.ensure_container(path[:n], pair)
    _t_value(em, path, named[-1], pair)


def _walk_toml(em: _Emitter, root: Node) -> None:
    arrays: dict[_Path, int] = {}
    base: _Path = ()
    for child in root.named_children:
        if em.full:
            return
        if child.type == "pair":
            _t_pair(em, base, child)
        elif child.type in ("table", "table_array_element"):
            key = next((c for c in child.named_children if c.type in _TOML_KEYS), None)
            if key is None:
                continue
            is_array = child.type == "table_array_element"
            parts = _t_key_parts(key)
            resolved: _Path = ()
            for j, part in enumerate(parts):
                resolved = (*resolved, part)
                last = j == len(parts) - 1
                if is_array and last:
                    arrays[resolved] = arrays.get(resolved, 0) + 1
                if resolved in arrays:
                    resolved = (*resolved, arrays[resolved] - 1)
            for n in range(1, len(resolved)):
                em.ensure_container(resolved[:n], child)
            if is_array:
                em.container(resolved, child)
            else:
                em.ensure_container(resolved, child)
            base = resolved
            for p in child.named_children:
                if p.type == "pair":
                    _t_pair(em, base, p)
                    if em.full:
                        return


# ---------------------------------------------------------------- entry point

_WALKERS: dict[_Lang, tuple[Callable[[], object], Callable[[_Emitter, Node], None]]] = {
    "yaml": (tree_sitter_yaml.language, _walk_yaml),
    "json": (tree_sitter_json.language, _walk_json),
    "toml": (tree_sitter_toml.language, _walk_toml),
}


def index_config(path: str, source: bytes) -> FileIndex:
    """Index a YAML/JSON/TOML file. Raises ValueError for any other extension."""
    lang = _EXT.get(PurePosixPath(path).suffix.lower())
    if lang is None:
        raise ValueError(f"unsupported config extension: {path!r}")
    capsule, walk = _WALKERS[lang]
    parser = Parser(Language(capsule()))
    root = parser.parse(source.decode("utf-8", errors="replace").encode()).root_node
    em = _Emitter(file_path=path)
    parse_errors = root.has_error
    try:
        walk(em, root)
    except RecursionError:
        parse_errors = True
    n_lines = source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)
    return FileIndex(
        path=path,
        language=lang,
        sha256=hashlib.sha256(source).hexdigest(),
        n_lines=n_lines,
        symbols=em.symbols,
        parse_errors=parse_errors,
    )
