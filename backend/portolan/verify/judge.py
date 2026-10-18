"""llm_judge (spec §10.9.3c): paper passage against retrieved code, in small batches."""

from __future__ import annotations

from typing import Any

from portolan.llm.gateway import Gateway
from portolan.llm.prompts import claim_judge
from portolan.llm.prompts.verify_schemas import JudgeOut
from portolan.models.paper import PaperDoc
from portolan.verify.localize import CodeContext
from portolan.verify.types import Claim, Decision

JUDGE_BATCH = 4
PASSAGE_CHARS = 900
NO_CODE_CONFIDENCE = 0.4


def _passage(doc: PaperDoc, claim: Claim) -> dict[str, str]:
    a = doc.anchor(claim.anchor)
    return {
        "id": claim.anchor,
        "label": a.label if a else "",
        "text": (a.text if a else claim.text)[:PASSAGE_CHARS],
    }


async def judge_batch(
    doc: PaperDoc,
    gateway: Gateway,
    ctx: CodeContext,
    claims: list[Claim],
    threshold: float,
) -> dict[str, Decision | None]:
    """One decision per claim key; None means the model's answer couldn't be trusted (dropped)."""
    decisions: dict[str, Decision | None] = {}
    items: list[dict[str, Any]] = []
    for claim in claims:
        candidates = ctx.candidates(claim)
        if not candidates:  # nothing to show the model: no request
            decisions[claim.key] = Decision(
                "not_found",
                NO_CODE_CONFIDENCE,
                "llm_judge",
                "No code related to this claim was found.",
            )
            continue
        items.append(
            {
                "key": claim.key,
                "claim": claim.text,
                "claim_type": claim.claim_type,
                "subject": claim.subject,
                "value": claim.value,
                "passage": _passage(doc, claim),
                "candidates": candidates,
            }
        )
    if not items:
        return decisions

    result = await gateway.call(
        "strong",
        claim_judge.TEMPLATE,
        {"paper_title": doc.title, "items": items},
        JudgeOut,
        "P2",
        doc.paper_id,
    )
    verdicts = {v.key: v for v in result.parsed.verdicts}  # type: ignore[attr-defined]
    for item in items:
        key = str(item["key"])
        v = verdicts.get(key)
        if v is None:
            decisions[key] = None  # the model skipped it: nothing learned, nothing to show
            continue
        confidence = min(max(v.confidence, 0.0), 1.0)
        span = ctx.valid_span(v.path, v.start_line, v.end_line)
        paper_side, code_side = v.paper_side.strip(), v.code_side.strip()
        verdict = v.verdict
        if verdict == "mismatch":
            if not paper_side or not code_side or span is None:
                decisions[key] = None  # no cited evidence on both sides: never surface it
                continue
            if confidence < threshold:
                verdict = "uncertain"  # precision first (§10.9.5)
        decisions[key] = Decision(
            verdict,
            confidence,
            "llm_judge",
            code_side or "No related code was found.",
            [span] if span else [],
            paper_side=paper_side or None,
        )
    return decisions
