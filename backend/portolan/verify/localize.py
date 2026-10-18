"""Find the code a claim is about (spec §10.9.2): named values and retrieved spans."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from portolan.code.index_store import IndexStore
from portolan.code.retrieval import retrieve
from portolan.models.code import CodeIndex, CodeSpan, Symbol
from portolan.store.db import Database
from portolan.verify.types import Claim, ValueItem
from portolan.verify.values import parse_value

RRF_K = 60
SPANS_PER_QUERY = 4
CANDIDATES = 3
CANDIDATE_LINES = 40
SIGNATURE_LINES = 25


def latest_commit(db: Database, repo_id: str) -> str | None:
    row = db.fetchone(
        "SELECT commit_sha FROM code_indexes WHERE repo_id = ? ORDER BY created_at DESC LIMIT 1",
        (repo_id,),
    )
    return row["commit_sha"] if row else None


def is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    return any(p in ("test", "tests", "testing") for p in parts[:-1]) or parts[-1].startswith(
        "test_"
    )


def signature_defaults(source: str) -> list[tuple[str, str]]:
    """(parameter, default source) pairs from the text of a `def`; [] if it won't parse."""
    depth, end = 0, -1
    for i, ch in enumerate(source):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0 and i > 0:
            end = i
            break
    if end < 0:
        return []
    header = source[: end + 1].strip()
    try:
        tree = ast.parse(header + "\n    pass")
    except SyntaxError:
        return []
    fn = tree.body[0] if tree.body else None
    if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
        return []
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    out: list[tuple[str, str]] = []
    for arg, default in zip(
        positional[len(positional) - len(args.defaults) :], args.defaults, strict=True
    ):
        out.append((arg.arg, ast.unparse(default)))
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if kw_default is not None:
            out.append((arg.arg, ast.unparse(kw_default)))
    return out


def _config_name(qualname: str) -> str:
    last = qualname.rsplit(".", 1)[-1]
    return last.split("[", 1)[0] or qualname


@dataclass
class CodeContext:
    """Everything the checks need to read the linked code at one commit."""

    store: IndexStore
    repo_id: str
    sha: str
    index: CodeIndex
    items: list[ValueItem] = field(default_factory=list[ValueItem])
    env: dict[str, float] = field(default_factory=dict[str, float])
    python_files: list[str] = field(default_factory=list[str])

    @classmethod
    def build(cls, store: IndexStore, index: CodeIndex) -> CodeContext:
        ctx = cls(store, index.repo_id, index.commit_sha, index)
        ctx.python_files = [p for p in index.files if p.endswith(".py") and not is_test_path(p)]
        for sym in store.symbols(index.repo_id, index.commit_sha):
            if is_test_path(sym.path):
                continue
            if sym.kind == "constant" and sym.value is not None:
                ctx.items.append(ValueItem(sym.name, sym.value, sym, sym.start_line, "constant"))
            elif sym.kind == "config_key" and sym.value is not None:
                name = _config_name(sym.qualname)
                ctx.items.append(ValueItem(name, sym.value, sym, sym.start_line, "config"))
            elif sym.kind == "cli_default" and sym.value is not None:
                ctx.items.append(ValueItem(sym.name, sym.value, sym, sym.start_line, "cli"))
            elif sym.kind in ("function", "method"):
                lines = store.lines(
                    index.repo_id,
                    index.commit_sha,
                    sym.path,
                    sym.start_line,
                    min(sym.end_line, sym.start_line + SIGNATURE_LINES),
                )
                for name, default in signature_defaults("\n".join(lines)):
                    ctx.items.append(ValueItem(name, default, sym, sym.start_line, "default"))
        # Two passes so "d_k = d_model // 8" can use d_model, whichever comes first.
        for _ in range(2):
            for item in ctx.items:
                if item.name not in ctx.env:
                    number = parse_value(item.value, ctx.env)
                    if number is not None:
                        ctx.env[item.name] = number
        return ctx

    def span(self, path: str, start: int, end: int | None = None) -> CodeSpan:
        return CodeSpan(
            repo_id=self.repo_id,
            commit_sha=self.sha,
            path=path,
            start_line=start,
            end_line=end if end is not None else start,
        )

    def valid_span(self, path: str | None, start: int | None, end: int | None) -> CodeSpan | None:
        """A span only if the file exists at this commit and the lines are inside it."""
        if not path or start is None:
            return None
        text = self.store.file_text(self.repo_id, self.sha, path.strip().removeprefix("./"))
        if text is None:
            return None
        total = len(text.splitlines())
        stop = end if end is not None else start
        if start < 1 or stop < start or start > total:
            return None
        return self.span(path.strip().removeprefix("./"), start, min(stop, total))

    def candidates(self, claim: Claim) -> list[dict[str, Any]]:
        """Retrieved code for the judge: numbered lines, tests and bare modules left out."""
        queries = [claim.subject, claim.text]  # not the value: a bare "3" matches anything
        score: dict[str, float] = {}
        by_id: dict[str, Symbol] = {}
        for query in queries:
            for rank, hit in enumerate(
                retrieve(self.store, self.repo_id, self.sha, query, k=SPANS_PER_QUERY), start=1
            ):
                s = hit.symbol
                if s.kind == "module" or is_test_path(s.path):
                    continue
                by_id[s.id] = s
                score[s.id] = score.get(s.id, 0.0) + 1.0 / (RRF_K + rank)
        order = sorted(score, key=lambda sid: (-score[sid], sid))[:CANDIDATES]
        out: list[dict[str, Any]] = []
        for sid in order:
            s = by_id[sid]
            end = min(s.end_line, s.start_line + CANDIDATE_LINES - 1)
            lines = self.store.lines(self.repo_id, self.sha, s.path, s.start_line, end)
            if lines:
                out.append(
                    {
                        "path": s.path,
                        "start_line": s.start_line,
                        "end_line": s.start_line + len(lines) - 1,
                        "qualname": s.qualname,
                        "lines": [[s.start_line + i, t] for i, t in enumerate(lines)],
                    }
                )
        return out

    def grep(self, pattern: Any) -> tuple[str, int] | None:
        """First (path, line) in the non-test Python files matching a compiled regex."""
        for path in self.python_files:
            text = self.store.file_text(self.repo_id, self.sha, path)
            if text is None:
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                if not line.lstrip().startswith("#") and pattern.search(line):
                    return path, n
        return None
