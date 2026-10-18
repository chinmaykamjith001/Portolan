"""Parsing and comparing values the way a person would (spec §10.9.3, literal_match).

"0.1", "1e-1", "10%", "5e-4", "eight" and "d_model // 8" are all just numbers. Everything
here is pure: no I/O, no LLM. Anything that can't be read as a number yields `None`, which makes
the caller fall through to the next rung of the ladder instead of guessing.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Callable, Mapping

from portolan.code.index_store import identifier_words

WORD_NUMBERS: dict[str, int] = {
    **{
        w: i
        for i, w in enumerate(
            ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
        )
    },
    "eleven": 11,
    "twelve": 12,
    "sixteen": 16,
    "twenty": 20,
    "thirty-two": 32,
    "sixty-four": 64,
}

_SCI_TIMES = re.compile(
    r"^\s*([0-9.]+)\s*(?:\u00d7|x|\\times|\*)\s*10\s*(?:\^|\*\*)\s*\{?\s*(-?[0-9]+)\s*\}?\s*$", re.I
)
_BINOPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Pow: lambda a, b: a**b,
    ast.Mod: lambda a, b: a % b,
}
MAX_EXPR_CHARS = 80
MAX_POWER = 64


def safe_eval(expr: str, env: Mapping[str, float] | None = None) -> float | None:
    """Evaluate plain arithmetic over numbers and known names. None for anything else."""
    text = expr.strip().rstrip(",")
    if not text or len(text) > MAX_EXPR_CHARS:
        return None
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError):
        return None

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ValueError
            return float(node.value)
        if isinstance(node, ast.Name):
            if env is None or node.id not in env:
                raise ValueError
            return float(env[node.id])
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            # config.d_model / self.d_model: use the bare name
            if env is None or node.attr not in env:
                raise ValueError
            return float(env[node.attr])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER:
                raise ValueError
            return _BINOPS[type(node.op)](left, right)
        raise ValueError

    try:
        result = walk(tree)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def parse_value(text: str | None, env: Mapping[str, float] | None = None) -> float | None:
    """A paper value or a code value as a number, or None when it isn't one."""
    if text is None:
        return None
    raw = text.strip().strip("`'\"").strip()
    if not raw:
        return None
    percent = raw.endswith("%")
    if percent:
        raw = raw[:-1].strip()
    word = raw.lower().replace(" ", "-")
    if word in WORD_NUMBERS:
        number: float | None = float(WORD_NUMBERS[word])
    else:
        m = _SCI_TIMES.match(raw)
        if m:
            number = float(m.group(1)) * 10 ** int(m.group(2))
        else:
            number = safe_eval(raw.replace(",", "") if raw.replace(",", "").isdigit() else raw, env)
    if number is None:
        return None
    return number / 100 if percent else number


def is_percent(text: str | None) -> bool:
    return text is not None and text.strip().endswith("%")


def numbers_equal(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-12)


def value_matches(
    claim: str | None, code: str | None, env: Mapping[str, float] | None
) -> bool | None:
    """True/False when both sides are numbers; None when either isn't (nothing to compare)."""
    want, have = parse_value(claim, env), parse_value(code, env)
    if want is None or have is None:
        return None
    if numbers_equal(want, have):
        return True
    # "10%" in the paper, 10 in the code (a percentage kept as a whole number)
    return is_percent(claim) and numbers_equal(want * 100, have)


# -- names -----------------------------------------------------------------------------------

_STOP = frozenset(
    [
        "the",
        "of",
        "a",
        "an",
        "in",
        "per",
        "each",
        "for",
        "to",
        "used",
        "use",
        "value",
        "values",
        "number",
        "num",
        "n",
        "count",
        "total",
        "size_of",
        "is",
        "are",
        "was",
        "with",
        "by",
        "at",
        "on",
        "all",
    ]
)
_ALIASES: dict[str, tuple[str, ...]] = {
    "lr": ("learning", "rate"),
    "eps": ("epsilon",),
    "dim": ("dimension",),
    "dims": ("dimension",),
    "dimensionality": ("dimension",),
    "nheads": ("heads",),
    "head": ("heads",),
    "layer": ("layers",),
    "blocks": ("layers",),
    "block": ("layers",),
    "depth": ("layers",),
    "iterations": ("steps",),
    "iters": ("steps",),
    "iter": ("steps",),
    "step": ("steps",),
    "prob": ("rate",),
    "probability": ("rate",),
    "p": ("rate",),
}


def canon_words(text: str) -> frozenset[str]:
    """Order-free, alias-folded words: "num_heads" and "number of heads" agree."""
    words: list[str] = []
    for w in identifier_words(text):
        if w in _STOP:
            continue
        words.extend(_ALIASES.get(w, (w,)))
    if "dropout" in words:  # "dropout rate", "dropout_prob" and "dropout" are one thing
        words = [w for w in words if w != "rate"]
    return frozenset(words)


def subject_views(subject: str) -> list[frozenset[str]]:
    """The subject as words, plus any identifier written inside it ("model size (d_model)")."""
    views = [canon_words(subject)]
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+", subject):
        views.append(canon_words(token))
    return [v for v in dict.fromkeys(views) if v]


def name_match(name: str, views: list[frozenset[str]]) -> str | None:
    """ "exact" when the name says the same thing as the subject; "weak" when one contains the
    other (attention_dropout vs dropout); None otherwise."""
    words = canon_words(name)
    if not words:
        return None
    if any(words == v for v in views):
        return "exact"
    if any(words < v or v < words for v in views):
        return "weak"
    return None
