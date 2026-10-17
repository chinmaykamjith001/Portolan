"""Python and notebook indexer (spec §10.4): symbols, imports and calls per file, via tree-sitter.

One iterative walk over the syntax tree. Docstrings are read in Python, not in a query, because the
grammar's docstring shape differs between parser sources (docs/notes/m3-externals.md §1.3).
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
from typing import NamedTuple, cast

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

from portolan.models.code import CallRef, FileIndex, FileLanguage, ImportRef, Symbol, SymbolKind

_MAX_VALUE_CHARS = 200
_NAME_TARGETS = ("pattern_list", "tuple_pattern")
_CLICK_OPTION = ("click.option", "option")


@functools.cache
def _language() -> Language:
    return Language(tree_sitter_python.language())


def _parse(source: bytes) -> Tree:
    # A Parser per call: it is cheap and keeps the module free of shared mutable state.
    # Callers must hold the Tree while using its nodes; nodes of a freed tree crash the process.
    return Parser(_language()).parse(source)


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


# Index the Point tuple: on tree-sitter 0.26.0 (Windows) `Point.row` returns garbage and corrupts
# the heap, which later crashes the process with an access violation.
def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _truncate(value: str) -> str:
    return value[:_MAX_VALUE_CHARS]


def _n_lines(text: str) -> int:
    return len(text.splitlines())


def _string_value(node: Node) -> str | None:
    """The literal value of a plain (non-f) string node, escapes left as written."""
    if node.type != "string" or any(c.type == "interpolation" for c in node.children):
        return None
    return "".join(_text(c) for c in node.children if c.type == "string_content")


def _first_statement(block: Node | None) -> Node | None:
    if block is None:
        return None
    return next((c for c in block.named_children if c.type != "comment"), None)


def _docstring(block: Node | None) -> str | None:
    """Docstring of a module or def body, with or without an expression_statement wrapper."""
    first = _first_statement(block)
    if first is not None and first.type == "expression_statement" and first.named_children:
        first = first.named_children[0]
    if first is None:
        return None
    raw = _string_value(first)
    if raw is None:
        return None
    return inspect.cleandoc(raw) or None


def _dotted(node: Node | None) -> str | None:
    """The dotted text of a plain or attribute name, else None (calls, subscripts, literals...)."""
    parts: list[str] = []
    while node is not None and node.type == "attribute":
        attr = node.child_by_field_name("attribute")
        if attr is None:
            return None
        parts.append(_text(attr))
        node = node.child_by_field_name("object")
    if node is None or node.type != "identifier":
        return None
    parts.append(_text(node))
    return ".".join(reversed(parts))


def _target_names(target: Node) -> list[str]:
    if target.type == "identifier":
        return [_text(target)]
    if target.type in _NAME_TARGETS:
        names = [c for c in target.named_children if c.type != "comment"]
        if names and all(c.type == "identifier" for c in names):
            return [_text(c) for c in names]
    return []


class _Item(NamedTuple):
    node: Node
    scope: str | None  # qualname of the enclosing class/function; None at module level
    in_class: bool  # the nearest enclosing scope is a class
    top: bool  # a statement directly in a module or class body
    start_line: int | None = None  # decorated definitions: the first decorator's line


class _Indexer:
    def __init__(self, path: str) -> None:
        self.path = path
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRef] = []
        self.calls: list[CallRef] = []
        self._seen: dict[str, int] = {}

    def add_symbol(
        self,
        kind: SymbolKind,
        name: str,
        qualname: str,
        start_line: int,
        end_line: int,
        *,
        docstring: str | None = None,
        signature: str | None = None,
        value: str | None = None,
        parent: str | None = None,
    ) -> None:
        sid = f"{self.path}::{qualname}"
        count = self._seen.get(sid, 0) + 1
        self._seen[sid] = count
        if count > 1:
            sid = f"{sid}#{count}"
        self.symbols.append(
            Symbol(
                id=sid,
                kind=kind,
                name=name,
                qualname=qualname,
                path=self.path,
                start_line=start_line,
                end_line=end_line,
                docstring=docstring,
                signature=signature,
                value=value,
                parent=parent,
            )
        )

    def run(self, root: Node, n_lines: int) -> None:
        self.add_symbol("module", "", "", 1, max(1, n_lines), docstring=_docstring(root))
        stack = [_Item(c, None, False, True) for c in reversed(root.named_children)]
        while stack:
            item = stack.pop()
            stack.extend(reversed(self.visit(item)))

    def visit(self, item: _Item) -> list[_Item]:
        node, scope, in_class = item.node, item.scope, item.in_class
        kind = node.type
        if kind == "decorated_definition":
            definition = node.child_by_field_name("definition")
            out = [_Item(c, scope, in_class, False) for c in node.named_children if c != definition]
            if definition is not None:
                out.append(_Item(definition, scope, in_class, item.top, _line(node)))
            return out
        if kind in ("function_definition", "class_definition"):
            return self.visit_def(item)
        if kind == "import_statement":
            self.visit_import(node)
            return []
        if kind == "import_from_statement":
            self.visit_import_from(node)
            return []
        if kind == "expression_statement" and item.top:
            self.visit_assignment(node, scope)
        elif kind == "call":
            self.visit_call(node, scope)
        return [_Item(c, scope, in_class, False) for c in node.named_children]

    def visit_def(self, item: _Item) -> list[_Item]:
        node, scope = item.node, item.scope
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None:
            return [_Item(c, scope, item.in_class, False) for c in node.named_children]
        name = _text(name_node)
        qualname = f"{scope}.{name}" if scope else name
        is_class = node.type == "class_definition"
        kind: SymbolKind = "class" if is_class else "method" if item.in_class else "function"
        self.add_symbol(
            kind,
            name,
            qualname,
            item.start_line or _line(node),
            _end_line(node),
            docstring=_docstring(body),
            signature=_text(node).split("\n", 1)[0].strip(),
            parent=scope,
        )
        # Parameter defaults, return annotations and base classes evaluate in the outer scope.
        out = [
            _Item(c, scope, item.in_class, False)
            for c in node.named_children
            if c != name_node and c != body
        ]
        if body is not None:
            out.extend(_Item(c, qualname, is_class, is_class) for c in body.named_children)
        return out

    def visit_import(self, node: Node) -> None:
        for target in node.children_by_field_name("name"):
            if target.type == "aliased_import":
                module_node = target.child_by_field_name("name")
                alias_node = target.child_by_field_name("alias")
                module = _text(module_node) if module_node is not None else ""
                alias = _text(alias_node) if alias_node is not None else None
            else:
                module, alias = _text(target), None
            self.imports.append(
                ImportRef(path=self.path, line=_line(node), module=module, alias=alias)
            )

    def visit_import_from(self, node: Node) -> None:
        module_node = node.child_by_field_name("module_name")
        module = _text(module_node).replace(" ", "") if module_node is not None else ""
        plain: list[str] = []
        aliased: list[tuple[str, str]] = []
        for target in node.children_by_field_name("name"):
            if target.type == "aliased_import":
                name_node = target.child_by_field_name("name")
                alias_node = target.child_by_field_name("alias")
                if name_node is not None and alias_node is not None:
                    aliased.append((_text(name_node), _text(alias_node)))
            else:
                plain.append(_text(target))
        if any(c.type == "wildcard_import" for c in node.children):
            plain.append("*")
        line = _line(node)
        if plain or not aliased:
            self.imports.append(ImportRef(path=self.path, line=line, module=module, names=plain))
        for name, alias in aliased:
            self.imports.append(
                ImportRef(path=self.path, line=line, module=module, names=[name], alias=alias)
            )

    def visit_assignment(self, stmt: Node, scope: str | None) -> None:
        assignment = stmt.named_children[0] if stmt.named_children else None
        if assignment is None or assignment.type != "assignment":
            return
        targets: list[Node] = []
        right: Node | None = assignment
        while right is not None and right.type == "assignment":
            left = right.child_by_field_name("left")
            if left is not None:
                targets.append(left)
            right = right.child_by_field_name("right")
        if right is None:  # a bare annotation: `x: int`
            return
        value = _truncate(_text(right))
        for target in targets:
            for name in _target_names(target):
                self.add_symbol(
                    "constant",
                    name,
                    f"{scope}.{name}" if scope else name,
                    _line(stmt),
                    _end_line(stmt),
                    value=value,
                    parent=scope,
                )

    def visit_call(self, call: Node, scope: str | None) -> None:
        callee = _dotted(call.child_by_field_name("function"))
        if callee is None:
            return
        self.calls.append(CallRef(path=self.path, line=_line(call), callee=callee, in_symbol=scope))
        if callee == "add_argument" or callee.endswith(".add_argument"):
            self.visit_cli_option(call, "argparse", scope)
        elif callee in _CLICK_OPTION:
            self.visit_cli_option(call, "click", scope)

    def visit_cli_option(self, call: Node, source: str, scope: str | None) -> None:
        args = call.child_by_field_name("arguments")
        if args is None:
            return
        flags: list[str] = []
        default: Node | None = None
        for arg in args.named_children:
            if arg.type == "string":
                flag = _string_value(arg)
                if flag:
                    flags.append(flag)
            elif arg.type == "keyword_argument":
                key = arg.child_by_field_name("name")
                if key is not None and _text(key) == "default":
                    default = arg.child_by_field_name("value")
        if not flags or default is None:
            return
        name = next((f for f in flags if f.startswith("--")), flags[0])
        self.add_symbol(
            "cli_default",
            name,
            f"{source}:{name}",
            _line(call),
            _end_line(call),
            value=_truncate(_text(default)),
            parent=scope,
        )


def _index_text(
    path: str, raw: bytes, parse_source: bytes, n_lines: int, language: FileLanguage
) -> FileIndex:
    tree = _parse(parse_source)
    root = tree.root_node
    indexer = _Indexer(path)
    indexer.run(root, n_lines)
    return FileIndex(
        path=path,
        language=language,
        sha256=hashlib.sha256(raw).hexdigest(),
        n_lines=n_lines,
        symbols=indexer.symbols,
        imports=indexer.imports,
        calls=indexer.calls,
        parse_errors=root.has_error,
    )


def index_python(path: str, source: bytes) -> FileIndex:
    """Index one Python file. `path` is repo-relative POSIX; line numbers are 1-based."""
    text = source.decode("utf-8", errors="replace")
    return _index_text(path, source, source, _n_lines(text), "python")


def _cell_source(cell: dict[str, object]) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(s for s in cast(list[object], source) if isinstance(s, str))
    return source if isinstance(source, str) else ""


def _strip_magics(cell: str) -> str:
    """Blank out IPython magics and shell escapes, keeping the line count unchanged."""
    lines = cell.split("\n")
    if lines and lines[0].lstrip().startswith("%%"):  # a cell magic: the whole cell is not Python
        return "\n".join("" for _ in lines)
    return "\n".join("" if ln.lstrip().startswith(("%", "!")) else ln for ln in lines)


def index_notebook(path: str, raw: bytes) -> FileIndex:
    """Index a .ipynb: code cells joined with "\\n" into `virtual_text`; outputs are never read."""
    sha = hashlib.sha256(raw).hexdigest()
    try:
        data: object = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return FileIndex(path=path, language="notebook", sha256=sha, n_lines=0, parse_errors=True)
    cells: list[object] = []
    if isinstance(data, dict):
        found = cast(dict[str, object], data).get("cells")
        if isinstance(found, list):
            cells = cast(list[object], found)
    code = [
        _cell_source(cast(dict[str, object], c))
        for c in cells
        if isinstance(c, dict) and cast(dict[str, object], c).get("cell_type") == "code"
    ]
    virtual_text = "\n".join(code)
    parse_text = "\n".join(_strip_magics(c) for c in code)
    result = _index_text(path, raw, parse_text.encode("utf-8"), _n_lines(virtual_text), "notebook")
    result.virtual_text = virtual_text
    return result
