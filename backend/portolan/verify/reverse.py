"""Reverse pass (spec §10.9.4): code behavior the paper never describes."""

from __future__ import annotations

import hashlib
import re

from portolan.llm.gateway import Gateway
from portolan.llm.prompts import reverse_pass
from portolan.llm.prompts.verify_schemas import ReverseOut
from portolan.models.finding import Finding
from portolan.models.paper import PaperDoc
from portolan.verify.localize import CodeContext, is_test_path
from portolan.verify.types import Claim
from portolan.verify.values import canon_words

MAX_SNIPPETS = 8
WINDOW = 40
LEAD = 8
MIN_CONFIDENCE = 0.5

# Lines that tend to change results without appearing in a paper's method section.
RESULT_CHANGING = re.compile(
    r"clip_grad|clamp\(|\.clip\(|normalize\(|LayerNorm|BatchNorm|GroupNorm|RMSNorm|weight_decay|"
    r"warmup|\.detach\(\)|label_smoothing|accumulat|math\.sqrt\(|\*\s*0\.\d|temperature|"
    r"moving_average|\bema\b",
    re.I,
)


def finding_id(*parts: object) -> str:
    return "f-" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def snippets(ctx: CodeContext) -> list[dict[str, object]]:
    scored: list[tuple[int, str, int, dict[str, object]]] = []
    for sym in ctx.store.symbols(ctx.repo_id, ctx.sha, kinds=("function", "method")):
        if is_test_path(sym.path) or not sym.path.endswith(".py"):
            continue
        lines = ctx.store.lines(ctx.repo_id, ctx.sha, sym.path, sym.start_line, sym.end_line)
        hits = [i for i, line in enumerate(lines) if RESULT_CHANGING.search(line)]
        if not hits:
            continue
        first = max(0, hits[0] - LEAD)
        window = lines[first : first + WINDOW]
        start = sym.start_line + first
        scored.append(
            (
                -len(hits),
                sym.id,
                start,
                {
                    "path": sym.path,
                    "start_line": start,
                    "end_line": start + len(window) - 1,
                    "qualname": sym.qualname,
                    "lines": [[start + i, t] for i, t in enumerate(window)],
                },
            )
        )
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s for *_, s in scored[:MAX_SNIPPETS]]


async def reverse_pass_findings(
    doc: PaperDoc, gateway: Gateway, ctx: CodeContext, claims: list[Claim]
) -> list[Finding]:
    shown = snippets(ctx)
    if not shown:
        return []
    result = await gateway.call(
        "strong",
        reverse_pass.TEMPLATE,
        {
            "paper_title": doc.title,
            "described": [c.text for c in claims][: reverse_pass.DESCRIBED],
            "snippets": shown,
        },
        ReverseOut,
        "P2",
        doc.paper_id,
    )
    documented = [canon_words(c.subject) for c in claims]
    out: list[Finding] = []
    for item in result.parsed.items:  # type: ignore[attr-defined]
        span = ctx.valid_span(item.path, item.start_line, item.end_line)
        title, evidence = item.title.strip(), item.evidence.strip()
        confidence = min(max(item.confidence, 0.0), 1.0)
        if span is None or not title or not evidence or confidence < MIN_CONFIDENCE:
            continue
        words = canon_words(title)
        if any(words & d for d in documented if d):
            continue  # the paper does talk about this: not undocumented
        out.append(
            Finding(
                id=finding_id("reverse", span.path, span.start_line, title.lower()),
                claim_text=title,
                claim_type="other",
                paper_anchor="",
                code_spans=[span],
                verdict="undocumented_in_paper",
                severity=item.severity,
                confidence=confidence,
                method="llm_judge",
                evidence=evidence,
            )
        )
    return out
