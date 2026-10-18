"""A fake engine for `--dry-run` and tests: no network, no LLM, and no claim to measure anything.

For each gold discrepancy it makes a deterministic choice (by hashing the id): detect it with a
finding that points at the gold location, detect it with a differently worded finding, or miss it;
and it adds one false positive per record. The numbers it produces say nothing about Portolan.
"""

from __future__ import annotations

import hashlib

from portolan.evalharness.records import Discrepancy, EvalRecord
from portolan.evalharness.runner import EngineOutput
from portolan.models.code import CodeSpan
from portolan.models.finding import Finding


def _bucket(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest(), 16) % 3


class FakeEngine:
    version = "fake-engine"

    async def check(self, record: EvalRecord) -> EngineOutput:
        findings: list[Finding] = []
        for g in record.gold:
            choice = _bucket(g.id)
            if choice == 2:
                continue  # missed
            spans = (
                [_span(g, record)]
                if choice == 0 and g.code_file and g.code_start is not None
                else []
            )
            findings.append(
                Finding(
                    id=f"fake-{g.id}",
                    claim_text=g.description if choice == 1 else "The paper states a value.",
                    claim_type="hyperparameter",
                    paper_anchor="sec:1",
                    code_spans=spans,
                    verdict="mismatch",
                    severity="medium",
                    confidence=0.8,
                    method="literal_match" if choice == 0 else "llm_judge",
                    evidence=g.description if choice == 1 else "The code differs.",
                )
            )
        findings.append(
            Finding(
                id=f"fake-extra-{record.id}",
                claim_text="Unrelated claim about batch sizes.",
                claim_type="other",
                paper_anchor="sec:2",
                verdict="mismatch",
                severity="low",
                confidence=0.75,
                method="llm_judge",
                evidence="Nothing like it appears in the gold list.",
            )
        )
        return EngineOutput(findings=findings, requests_used=0, note="fake engine")


def _span(g: Discrepancy, record: EvalRecord) -> CodeSpan:
    start = g.code_start or 1
    return CodeSpan(
        repo_id="fake",
        commit_sha=record.repo_commit or "fake",
        path=g.code_file or "",
        start_line=start,
        end_line=g.code_end or start,
    )
