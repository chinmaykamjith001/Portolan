"""Verification engine (spec §10.9): paper claims against the linked code.

Order of work, cheapest first: extract claims (a few batched `strong` calls), check each with the
ladder (literal_match, static: no LLM), judge what is left in small batches, then scan the code
for undocumented behavior. Every step persists its result, so a stopped run resumes without
repeating LLM calls, and a finished run for the same commit does nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from portolan.code.index_store import IndexStore
from portolan.llm.gateway import Gateway
from portolan.models.finding import Finding, Severity
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.findings import FindingStore
from portolan.store.repos import chosen_repo
from portolan.verify.claims import extract_claims
from portolan.verify.issues import recount
from portolan.verify.judge import JUDGE_BATCH, judge_batch
from portolan.verify.literal import literal_check
from portolan.verify.localize import CodeContext, latest_commit
from portolan.verify.mapping import ConceptMapper
from portolan.verify.reverse import finding_id, reverse_pass_findings
from portolan.verify.static import static_check
from portolan.verify.types import Claim, Decision

SEVERITY: dict[str, Severity] = {
    "architecture": "high",
    "objective": "high",
    "algorithm_step": "high",
    "hyperparameter": "medium",
    "data_processing": "medium",
    "evaluation": "medium",
    "other": "low",
}


def _severity(claim: Claim, d: Decision) -> Severity:
    if d.severity is not None:
        return d.severity
    return SEVERITY[claim.claim_type] if d.verdict == "mismatch" else "low"


class _Progress:
    """Which claims are settled and whether the reverse pass ran; stored between steps."""

    def __init__(self, raw: str | None) -> None:
        data: dict[str, Any] = json.loads(raw) if raw else {}
        self.checked: set[str] = set(data.get("checked", []))
        self.reverse: bool = bool(data.get("reverse", False))

    def dump(self) -> str:
        return json.dumps({"checked": sorted(self.checked), "reverse": self.reverse})


async def verify_paper(
    doc: PaperDoc,
    gateway: Gateway,
    chart: ChartStore,
    db: Database,
    on_finding: Callable[[Finding], None],
) -> None:
    """Check the paper against its linked code. Saves each finding as it is decided, keeps
    `issue_count_self` current, and calls `on_finding` for every new finding. No repo, or a
    repo that isn't indexed, ends quietly with no findings."""
    paper_id = doc.paper_id
    findings = FindingStore(db)
    repo = chosen_repo(db, paper_id)
    sha = latest_commit(db, repo.id) if repo else None
    store = IndexStore(db)
    index = store.load_index(repo.id, sha) if repo and sha else None
    if repo is None or sha is None or index is None:
        findings.set_run(paper_id, "done")
        return

    threshold = gateway.config.generation.mismatch_confidence_threshold
    data = findings.run_data(paper_id)
    if data.get("repo_commit") not in (None, sha):  # the code changed: start over
        findings.delete_paper(paper_id)
        recount(chart, paper_id, [], threshold)
        data = {}
    if data.get("state") == "done":
        return
    findings.set_run(paper_id, "running", repo_commit=sha)

    ctx = CodeContext.build(store, index)
    mapper = ConceptMapper(doc, chart)
    known = {f.id for f in findings.all(paper_id)}
    progress = _Progress(data.get("progress_json"))

    def emit(f: Finding) -> None:
        f.concept_ids = mapper.map(f.paper_anchor, f"{f.claim_text} {f.evidence}")
        findings.save(paper_id, f)
        recount(chart, paper_id, findings.all(paper_id), threshold)
        if f.id not in known:
            known.add(f.id)
            on_finding(f)

    def settle(claim: Claim, d: Decision | None) -> None:
        progress.checked.add(claim.key)
        if d is None:
            return
        label = a.label if (a := doc.anchor(claim.anchor)) else claim.anchor
        emit(
            Finding(
                id=finding_id(claim.anchor, claim.text),
                claim_text=claim.text,
                claim_type=claim.claim_type,
                paper_anchor=claim.anchor,
                code_spans=d.spans,
                verdict=d.verdict,
                severity=_severity(claim, d),
                confidence=d.confidence,
                method=d.method,
                evidence=f"Paper ({label}): {d.paper_side or claim.text} Code: {d.code_side}",
            )
        )

    try:
        stored_claims = data.get("claims_json")
        if stored_claims:
            claims = [Claim.model_validate(c) for c in json.loads(stored_claims)]
        else:
            claims = await extract_claims(doc, gateway)
            findings.set_run(
                paper_id, "running", claims_json=json.dumps([c.model_dump() for c in claims])
            )

        pending: list[Claim] = []
        for claim in claims:
            if claim.key in progress.checked:
                continue
            decision = literal_check(ctx, claim) or static_check(ctx, claim)
            if decision is None:
                pending.append(claim)
            else:
                settle(claim, decision)
        findings.set_run(paper_id, "running", progress_json=progress.dump())

        for start in range(0, len(pending), JUDGE_BATCH):
            batch = pending[start : start + JUDGE_BATCH]
            decided = await judge_batch(doc, gateway, ctx, batch, threshold)
            for claim in batch:
                settle(claim, decided.get(claim.key))
            findings.set_run(paper_id, "running", progress_json=progress.dump())

        if not progress.reverse:
            for f in await reverse_pass_findings(doc, gateway, ctx, claims):
                emit(f)
            progress.reverse = True
        findings.set_run(paper_id, "done", progress_json=progress.dump())
    except BaseException:
        findings.set_run(paper_id, "failed", progress_json=progress.dump())
        raise
