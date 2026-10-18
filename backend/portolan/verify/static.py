"""static (spec §10.9.3b): structural checks read straight from the code. No LLM.

Two checks: "N layers" claims (the count in a `ModuleList` / `range(N)` / `clones(layer, N)`),
and "the model uses X" claims (an operation such as LayerNorm or dropout is present in the code).
Both only decide when they can; otherwise the claim goes on to the judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from portolan.code.index_store import identifier_words
from portolan.models.code import Symbol
from portolan.verify.localize import CodeContext, is_test_path, signature_defaults
from portolan.verify.types import Claim, Decision
from portolan.verify.values import parse_value

CONF_COUNT_MATCH = 0.85
CONF_COUNT_MISMATCH = 0.85
CONF_PRESENT = 0.8
CONF_ABSENT = 0.75

_LAYER_WORDS = frozenset(
    ["layer", "layers", "block", "blocks", "stack", "stacked", "identical", "depth"]
)
_GENERIC = frozenset(
    [
        "number",
        "of",
        "the",
        "in",
        "each",
        "a",
        "an",
        "per",
        "with",
        "by",
        "n",
        "total",
        "stack",
        "stacked",
        "identical",
        "layer",
        "layers",
        "block",
        "blocks",
        "depth",
    ]
)
_MAKES_LAYERS = re.compile(r"ModuleList|Sequential|clones\(|_get_clones|for\s+_\s+in\s+range")
_RANGE = re.compile(r"range\(\s*([^()]*?)\s*\)")
_CLONES = re.compile(r"clones?\(\s*[^,()]+(?:\([^()]*\))?\s*,\s*([^()]*?)\s*\)")
_NEGATION = re.compile(
    r"\b(no|not|without|never|neither|nor|omit|omits|omitted|remove|removed)\b|n't", re.I
)

# Operations a claim can name, and how they look in code.
OPERATIONS: dict[str, re.Pattern[str]] = {
    "layer normalization": re.compile(r"LayerNorm|layer_norm|layernorm|RMSNorm", re.I),
    "batch normalization": re.compile(r"BatchNorm|batch_norm", re.I),
    "dropout": re.compile(r"Dropout|\bdropout\s*\(|F\.dropout", re.I),
    "label smoothing": re.compile(r"label_smoothing|LabelSmoothing|smoothing", re.I),
    "gradient clipping": re.compile(r"clip_grad|clip_gradient|max_grad_norm", re.I),
    "weight decay": re.compile(r"weight_decay", re.I),
    "warmup": re.compile(r"warmup|warm_up", re.I),
}
_OP_ALIASES = {
    "layer norm": "layer normalization",
    "layernorm": "layer normalization",
    "layer-norm": "layer normalization",
    "batch norm": "batch normalization",
    "batchnorm": "batch normalization",
    "lr warmup": "warmup",
    "warm-up": "warmup",
    "grad clipping": "gradient clipping",
    "gradient clip": "gradient clipping",
}


@dataclass
class _Count:
    symbol: Symbol
    value: int
    source: str
    line: int


def _body_env(ctx: CodeContext, symbol: Symbol) -> dict[str, float]:
    """Constants plus the parameter defaults of the symbol and of its class's __init__."""
    env = dict(ctx.env)
    owners = [symbol]
    if symbol.parent:
        init = ctx.store.symbol(ctx.repo_id, ctx.sha, f"{symbol.path}::{symbol.parent}.__init__")
        if init is not None:
            owners.append(init)
    for owner in owners:
        lines = ctx.store.lines(
            ctx.repo_id,
            ctx.sha,
            owner.path,
            owner.start_line,
            min(owner.end_line, owner.start_line + 25),
        )
        for name, default in signature_defaults("\n".join(lines)):
            number = parse_value(default, env)
            if number is not None:
                env[name] = number
    return env


def _counts(ctx: CodeContext) -> list[_Count]:
    found: list[_Count] = []
    for sym in ctx.store.symbols(ctx.repo_id, ctx.sha, kinds=("class", "function", "method")):
        if is_test_path(sym.path):
            continue
        lines = ctx.store.lines(ctx.repo_id, ctx.sha, sym.path, sym.start_line, sym.end_line)
        body = "\n".join(lines)
        if not _MAKES_LAYERS.search(body):
            continue
        env = _body_env(ctx, sym)
        for offset, line in enumerate(lines):
            if not _MAKES_LAYERS.search(line) and not (
                offset and _MAKES_LAYERS.search(lines[offset - 1])
            ):
                continue
            for pattern in (_RANGE, _CLONES):
                for m in pattern.finditer(line):
                    value = parse_value(m.group(1), env)
                    if value is not None and value == int(value) and 1 <= value <= 1000:
                        found.append(_Count(sym, int(value), m.group(0), sym.start_line + offset))
    return found


def _context_words(claim: Claim) -> frozenset[str]:
    words = set(identifier_words(claim.subject)) - _GENERIC
    return frozenset(w for w in words if len(w) > 1)


def _layer_count(ctx: CodeContext, claim: Claim) -> Decision | None:
    if claim.value is None or not (set(identifier_words(claim.subject)) & _LAYER_WORDS):
        return None
    want = parse_value(claim.value)
    if want is None or want != int(want):
        return None
    context = _context_words(claim)
    relevant: list[_Count] = []
    for c in _counts(ctx):
        names = set(identifier_words(f"{c.symbol.qualname} {c.symbol.path}"))
        if not context or context <= names:
            relevant.append(c)
    if not relevant:
        return None
    for c in relevant:
        if c.value == int(want):
            return Decision(
                "matches",
                CONF_COUNT_MATCH,
                "static",
                f"{c.symbol.qualname} builds {c.value} layers ({c.source}) "
                f"at {c.symbol.path}:{c.line}.",
                [ctx.span(c.symbol.path, c.line)],
            )
    if not context:
        return None  # "N layers" with no further hint: too many places could be meant
    first = relevant[0]
    return Decision(
        "mismatch",
        CONF_COUNT_MISMATCH,
        "static",
        f"{first.symbol.qualname} builds {first.value} layers ({first.source}) "
        f"at {first.symbol.path}:{first.line}.",
        [ctx.span(first.symbol.path, first.line)],
    )


def _operation(claim: Claim) -> str | None:
    text = f"{claim.subject} {claim.text}".lower()
    for alias, name in _OP_ALIASES.items():
        text = text.replace(alias, name)
    return next((name for name in OPERATIONS if name in text), None)


def _presence(ctx: CodeContext, claim: Claim) -> Decision | None:
    if claim.value is not None or _NEGATION.search(f"{claim.subject} {claim.text}"):
        return None
    op = _operation(claim)
    if op is None:
        return None
    hit = ctx.grep(OPERATIONS[op])
    if hit is not None:
        path, line = hit
        return Decision(
            "matches",
            CONF_PRESENT,
            "static",
            f"The code uses {op} at {path}:{line}.",
            [ctx.span(path, line)],
        )
    return Decision(
        "mismatch", CONF_ABSENT, "static", f"The code never uses {op}: no match in any source file."
    )


def static_check(ctx: CodeContext, claim: Claim) -> Decision | None:
    return _layer_count(ctx, claim) or _presence(ctx, claim)
