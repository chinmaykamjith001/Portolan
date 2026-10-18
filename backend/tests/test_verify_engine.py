"""Verification engine (spec §10.9, M8 acceptance) on a hand-made paper and a planted toy repo.

The LLM here is SCRIPTED: its replies are written by the test. These tests prove the pipeline
(extraction -> literal/static -> judge -> reverse pass -> findings, issue counts, resume,
thresholds); they say nothing about how well a real model judges claims.
"""

from __future__ import annotations

import pytest

from portolan.llm.types import LLMError
from portolan.models.finding import Finding
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.findings import FindingStore
from portolan.verify.engine import verify_paper
from tests.verify_helpers import (
    CLAIMS,
    CORRECT,
    PAPER_ID,
    PLANTED,
    ScriptedVerify,
    anchor_of,
    extract_reply,
    full_script,
    gateway,
    judge_reply,
    link_repo,
    paper,
    reverse_reply,
    setup_chart,
)

THRESHOLD = 0.7


@pytest.fixture
def chart(db: Database) -> ChartStore:
    return setup_chart(db)


def by_claim(findings: list[Finding], name: str) -> Finding:
    anchor = anchor_of(paper(), CLAIMS[name][0])
    return next(f for f in findings if f.paper_anchor == anchor)


async def run(db: Database, chart: ChartStore, provider: ScriptedVerify) -> list[Finding]:
    seen: list[Finding] = []
    await verify_paper(paper(), gateway(db, provider), chart, db, seen.append)
    return seen


async def test_finds_the_planted_mismatches_and_no_false_ones(
    db: Database, chart: ChartStore
) -> None:
    link_repo(db)
    provider = full_script(paper())
    emitted = await run(db, chart, provider)
    stored = FindingStore(db).all(PAPER_ID)

    mismatches = {f.paper_anchor for f in stored if f.verdict == "mismatch"}
    planted = {anchor_of(paper(), CLAIMS[n][0]) for n in PLANTED}
    assert planted <= mismatches and len(mismatches & planted) == 3  # all 3 planted found
    assert mismatches == planted  # and nothing else: no false mismatch among the correct claims

    for name in CORRECT:
        f = by_claim(stored, name)
        assert f.verdict == "matches", (name, f.verdict, f.evidence)
        assert f.verdict != "mismatch"

    # Which rung decided each one: the first two need no LLM at all.
    assert by_claim(stored, "dropout").method == "literal_match"
    assert by_claim(stored, "decoder").method == "static"
    assert by_claim(stored, "scaling").method == "llm_judge"
    assert by_claim(stored, "lr").method == "literal_match"  # 0.001 in the paper, 1e-3 in the code
    assert by_claim(stored, "heads").method == "literal_match"  # "eight" against num_heads = 8
    assert by_claim(stored, "layernorm").method == "static"

    # Evidence names both sides, and the spans point at real lines.
    dropout = by_claim(stored, "dropout")
    assert "DROPOUT = 0.3" in dropout.evidence and "0.1" in dropout.evidence
    assert dropout.code_spans and dropout.code_spans[0].path == "toy/model.py"
    assert dropout.confidence >= THRESHOLD and dropout.severity == "medium"

    # The reverse pass adds one undocumented behavior, which is not a node warning.
    undocumented = [f for f in stored if f.verdict == "undocumented_in_paper"]
    assert len(undocumented) == 1 and "clipping" in undocumented[0].claim_text.lower()
    assert undocumented[0].severity in ("low", "medium")

    # on_finding fired once per finding.
    assert {f.id for f in emitted} == {f.id for f in stored} and len(emitted) == len(stored)
    assert FindingStore(db).run_state(PAPER_ID) == "done"
    # claims_extract 1 + claim_judge 1 (two claims in one batch) + reverse_scan 1
    assert provider.calls == {"claims_extract": 1, "claim_judge": 1, "reverse_scan": 1}


async def test_findings_map_to_concepts_and_set_issue_counts(
    db: Database, chart: ChartStore
) -> None:
    link_repo(db)
    await run(db, chart, full_script(paper()))
    stored = FindingStore(db).all(PAPER_ID)
    assert by_claim(stored, "decoder").concept_ids == ["decoder-stack"]
    assert by_claim(stored, "scaling").concept_ids == ["attention"]
    assert by_claim(stored, "dropout").concept_ids == ["training"]
    counts = {c.id: c.issue_count_self for c in chart.concepts(PAPER_ID).values()}
    assert counts == {
        "decoder-stack": 1,
        "attention": 1,
        "training": 1,
    }  # undocumented: not counted


def forbid_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("claims were extracted again")

    monkeypatch.setattr("portolan.verify.engine.extract_claims", boom)


async def test_second_run_after_done_does_nothing(
    db: Database, chart: ChartStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    link_repo(db)
    await run(db, chart, full_script(paper()))
    forbid_extraction(monkeypatch)
    again = ScriptedVerify()  # any call would raise KeyError
    emitted = await run(db, chart, again)
    assert emitted == [] and again.total == 0
    assert len(FindingStore(db).all(PAPER_ID)) == 9


async def test_a_stopped_run_resumes_without_repeating_work(
    db: Database, chart: ChartStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    link_repo(db)
    doc = paper()
    first = ScriptedVerify(
        claims_extract=[extract_reply(doc)], claim_judge=[LLMError("The provider is down.")]
    )
    with pytest.raises(LLMError):
        await run(db, chart, first)
    store = FindingStore(db)
    assert store.run_state(PAPER_ID) == "failed"
    kept = {f.id for f in store.all(PAPER_ID)}
    assert len(kept) == 6  # everything literal_match and static could decide was already saved

    forbid_extraction(monkeypatch)  # the claims were stored with the run
    second = ScriptedVerify(claim_judge=[judge_reply(doc)], reverse_scan=[reverse_reply()])
    emitted = await run(db, chart, second)
    assert second.calls == {"claim_judge": 1, "reverse_scan": 1}
    assert {f.id for f in emitted}.isdisjoint(kept)  # only the new findings are announced
    assert len(store.all(PAPER_ID)) == 9 and store.run_state(PAPER_ID) == "done"


async def test_no_repo_ends_quietly_without_any_request(db: Database, chart: ChartStore) -> None:
    provider = ScriptedVerify()
    assert await run(db, chart, provider) == []
    assert provider.total == 0
    assert FindingStore(db).run_state(PAPER_ID) == "done"
    assert FindingStore(db).all(PAPER_ID) == []


async def test_repo_that_is_not_indexed_yet_ends_quietly(db: Database, chart: ChartStore) -> None:
    link_repo(db, index=False)
    provider = ScriptedVerify()
    assert await run(db, chart, provider) == []
    assert provider.total == 0 and FindingStore(db).run_state(PAPER_ID) == "done"


async def test_invented_anchors_are_not_claims(db: Database, chart: ChartStore) -> None:
    link_repo(db)
    doc = paper()
    reply = extract_reply(doc, ("dropout",))
    reply["claims"].append(
        {"anchor": "para:99:1", "claim_type": "other", "subject": "dropout rate", "value": "0.5",
         "text": "Made up."}
    )  # fmt: skip
    provider = ScriptedVerify(claims_extract=[reply], reverse_scan=[{"items": []}])
    await run(db, chart, provider)
    stored = FindingStore(db).all(PAPER_ID)
    assert [f.paper_anchor for f in stored] == [anchor_of(doc, CLAIMS["dropout"][0])]


async def test_reverse_pass_skips_what_the_paper_already_describes(
    db: Database, chart: ChartStore
) -> None:
    link_repo(db)
    doc = paper()
    reply = reverse_reply()
    reply["items"].append(
        {"title": "Dropout rate", "evidence": "model.py sets a dropout the paper describes.",
         "path": "toy/model.py", "start_line": 5, "end_line": 5, "severity": "low",
         "confidence": 0.9}
    )  # fmt: skip
    provider = ScriptedVerify(
        claims_extract=[extract_reply(doc, ("dropout",))], reverse_scan=[reply]
    )
    await run(db, chart, provider)
    titles = [
        f.claim_text for f in FindingStore(db).all(PAPER_ID) if f.verdict == "undocumented_in_paper"
    ]
    assert titles == ["Gradient clipping at 1.0"]  # "Dropout rate" is a documented claim


async def test_new_commit_starts_over(db: Database, chart: ChartStore) -> None:
    link_repo(db)
    await run(db, chart, full_script(paper()))
    store = FindingStore(db)
    store.set_run(PAPER_ID, "done", repo_commit="c" * 40)  # as if the code had moved on
    provider = ScriptedVerify()  # the identical prompts are answered from the gateway cache
    emitted = await run(db, chart, provider)
    assert provider.total == 0
    assert len(emitted) == 9  # a fresh run announces every finding again
    assert store.run_data(PAPER_ID)["repo_commit"] == "b" * 40
