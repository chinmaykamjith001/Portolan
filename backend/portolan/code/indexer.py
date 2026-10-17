"""Index a checked-out repository (spec §8 `code.indexer`, §10.4).

Walks the checkout (skipping vendored/third-party folders, binaries, and files over 1 MB),
indexes Python files, notebooks (code cells only) and YAML/JSON/TOML configs, works out which
imports are libraries (they don't resolve inside the repo), and stores everything for retrieval.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from portolan.code.config_index import index_config
from portolan.code.index_store import IndexStore
from portolan.code.python_index import index_notebook, index_python
from portolan.models.code import CallRef, CodeIndex, FileIndex, ImportRef

MAX_FILE_BYTES = 1_000_000
MAX_FILES = 5_000
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".tox", ".nox", ".eggs", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "__pycache__", "node_modules", ".venv", "venv", "env", "site-packages", "build", "dist",
    "third_party", "third-party", "thirdparty", "vendor", "vendored", "external", "extern", "deps",
}  # fmt: skip
CONFIG_SUFFIXES = {".yaml", ".yml", ".json", ".toml"}
CONFIG_SKIP_NAMES = {"package-lock.json", "poetry.lock", "pnpm-lock.yaml", "yarn.lock"}


@dataclass
class BuiltIndex:
    index: CodeIndex
    files: list[FileIndex]
    texts: dict[str, str]
    kinds: Counter[str] = field(default_factory=Counter[str])


def _kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix == ".ipynb":
        return "notebook"
    if suffix in CONFIG_SUFFIXES and path.name.lower() not in CONFIG_SKIP_NAMES:
        return "config"
    return None


def long_path(path: Path) -> Path:
    r"""On Windows, use an extended-length path (\\?\C:\...) so paths over 260 chars work."""
    if os.name != "nt":
        return path
    resolved = str(path.resolve())
    return path if resolved.startswith("\\\\?\\") else Path("\\\\?\\" + resolved)


def walk(root: Path) -> tuple[list[tuple[str, Path, str]], dict[str, str]]:
    """(relative path, absolute path, kind) for every indexable file, plus skipped → reason."""
    found: list[tuple[str, Path, str]] = []
    skipped: dict[str, str] = {}
    root = long_path(root)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            rel = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                continue  # never follow links out of the checkout
            if entry.is_dir():
                if entry.name in SKIP_DIRS or entry.name.endswith(".egg-info"):
                    skipped[rel + "/"] = "vendored or generated folder"
                else:
                    stack.append(entry)
                continue
            kind = _kind(entry)
            if kind is None:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                skipped[rel] = "couldn't read the file"
                continue
            if size > MAX_FILE_BYTES:
                skipped[rel] = "larger than 1 MB"
                continue
            if len(found) >= MAX_FILES:
                skipped[rel] = f"over the {MAX_FILES:,}-file limit"
                continue
            found.append((rel, entry, kind))
    found.sort(key=lambda item: item[0])
    return found, skipped


def repo_module_names(paths: list[str]) -> set[str]:
    """Names an absolute import could resolve to inside the repo (files and folders, incl. src/)."""
    names: set[str] = set()
    for rel in paths:
        parts = rel.split("/")
        if parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]
        names.update(p for p in parts if p and p != "__init__")
    return names


def library_modules(imports: list[ImportRef], repo_names: set[str]) -> list[str]:
    """Top-level modules imported but not found in the repo or the standard library."""
    libs: set[str] = set()
    for imp in imports:
        if imp.module.startswith("."):
            continue
        top = imp.module.split(".")[0]
        if top and top not in repo_names and top not in sys.stdlib_module_names:
            libs.add(top)
    return sorted(libs)


def alias_table(file: FileIndex) -> dict[str, str]:
    """Local name → fully qualified import ("F" → "torch.nn.functional", "nn" → "torch.nn")."""
    table: dict[str, str] = {}
    for imp in file.imports:
        if imp.module.startswith("."):
            continue
        if imp.names:
            for name in imp.names:
                table[name] = f"{imp.module}.{name}"
            continue
        if imp.alias:
            table[imp.alias] = imp.module
        else:
            table[imp.module.split(".")[0]] = imp.module.split(".")[0]
    return table


def qualify_call(call: CallRef, aliases: dict[str, str]) -> str | None:
    """`F.softmax` → `torch.nn.functional.softmax` when the head is an imported name."""
    head, _, rest = call.callee.partition(".")
    target = aliases.get(head)
    if target is None:
        return None
    return f"{target}.{rest}" if rest else target


def library_calls(file: FileIndex, libraries: set[str]) -> list[tuple[CallRef, str]]:
    """Calls in this file that land in a third-party library, with their qualified names."""
    aliases = alias_table(file)
    out: list[tuple[CallRef, str]] = []
    for call in file.calls:
        qualified = qualify_call(call, aliases)
        if qualified and qualified.split(".")[0] in libraries:
            out.append((call, qualified))
    return out


def build_index(root: Path, repo_id: str, commit_sha: str) -> BuiltIndex:
    entries, skipped = walk(root)
    files: list[FileIndex] = []
    texts: dict[str, str] = {}
    kinds: Counter[str] = Counter()
    for rel, path, kind in entries:
        try:
            raw = path.read_bytes()
        except OSError:
            skipped[rel] = "couldn't read the file"
            continue
        if b"\x00" in raw[:8192]:
            skipped[rel] = "binary file"
            continue
        try:
            if kind == "python":
                file = index_python(rel, raw)
            elif kind == "notebook":
                file = index_notebook(rel, raw)
            else:
                file = index_config(rel, raw)
        except (ValueError, UnicodeDecodeError) as exc:
            skipped[rel] = f"couldn't parse ({type(exc).__name__})"
            continue
        files.append(file)
        texts[rel] = (
            file.virtual_text
            if file.virtual_text is not None
            else raw.decode("utf-8", errors="replace")
        )
        kinds[file.language] += 1

    names = repo_module_names([f.path for f in files if f.language == "python"])
    imports = [imp for f in files for imp in f.imports]
    index = CodeIndex(
        repo_id=repo_id,
        commit_sha=commit_sha,
        root=str(root),
        files=[f.path for f in files],
        symbol_count=sum(len(f.symbols) for f in files),
        library_modules=library_modules(imports, names),
        skipped=skipped,
    )
    return BuiltIndex(index=index, files=files, texts=texts, kinds=kinds)


def index_checkout(store: IndexStore, root: Path, repo_id: str, commit_sha: str) -> BuiltIndex:
    """Build and store the index for a checkout (replacing any earlier one for that commit)."""
    built = build_index(root, repo_id, commit_sha)
    store.save(built.index, built.files, built.texts)
    return built
