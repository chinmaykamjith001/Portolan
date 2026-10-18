"""Static check for LLM-written programs (spec §10.7.3). First layer; the child enforces more."""

from __future__ import annotations

import ast

MAX_SOURCE_BYTES = 8 * 1024

ALLOWED_MODULES = frozenset({"math", "statistics", "fractions", "json", "numpy"})

FORBIDDEN_NAMES = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "getattr",
        "setattr",
        "delattr",
        "vars",
        "globals",
        "locals",
        "breakpoint",
        "input",
        "help",
        "exit",
        "quit",
        "memoryview",
    }
)

# Attribute names that reach files, processes, interpreter internals or frames. numpy's file IO
# (load, save, fromfile, memmap, ...) is listed because the allowed `numpy` import exposes it.
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "open",
        "os",
        "sys",
        "subprocess",
        "socket",
        "system",
        "popen",
        "environ",
        "modules",
        "builtins",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_back",
        "f_code",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "tb_frame",
        "tb_next",
        "load",
        "loads",
        "save",
        "savez",
        "savez_compressed",
        "savetxt",
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "tofile",
        "fromregex",
        "memmap",
        "DataSource",
        "ctypeslib",
        "f2py",
        "testing",
        "distutils",
        "show_config",
        "show_runtime",
    }
)


def _not_allowed(name: str) -> str:
    return f"The example tried to use `{name}`, which isn't allowed."


def imported_modules(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def check_source(source: str) -> tuple[ast.Module | None, str | None]:
    """Parse and vet `source`: (tree, None) when fine, else (None, plain-language reason)."""
    if len(source.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:
        return None, "The example program is too long (the limit is 8 KB)."
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        where = f" on line {exc.lineno}" if exc.lineno else ""
        return None, f"The example has a syntax error{where}."
    except (ValueError, RecursionError):
        return None, "The example couldn't be read as Python."

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    return None, _not_allowed(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                return None, "The example used a relative import, which isn't allowed."
            root = node.module.split(".")[0]
            if root not in ALLOWED_MODULES:
                return None, _not_allowed(root)
            for alias in node.names:
                if alias.name in FORBIDDEN_ATTRIBUTES or alias.name.startswith("_"):
                    return None, _not_allowed(alias.name)
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
                return None, _not_allowed(node.id)
        elif isinstance(node, ast.Attribute):
            # A leading underscore is how stdlib modules hold their own os (statistics.random._os).
            if node.attr in FORBIDDEN_ATTRIBUTES or node.attr.startswith("_"):
                return None, _not_allowed(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            # "{0.__class__}".format(x) walks attributes without an ast.Attribute node.
            return None, "The example used a name with double underscores, which isn't allowed."
    return tree, None
