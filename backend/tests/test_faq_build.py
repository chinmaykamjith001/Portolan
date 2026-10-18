"""build_faq: collect → cluster → answer → link, with every source and the LLM faked."""

from __future__ import annotations

import httpx
import pytest
import respx

from portolan.faq import build
from portolan.faq.build import build_faq, link_concepts
from portolan.models.finding import FAQItem
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.faq import FaqStore
from portolan.store.findings import FindingStore
from portolan.store.repos import choose_repo
from tests.detail_helpers import PAPER_ID
from tests.faq_helpers import (
    GH,
    OR,
    FakeFaqLLM,
    concepts,
    faq_gateway,
    faq_paper,
    finding,
    http,
    mock_issues,
    mock_openreview,
    repo,
)


def setup(db: Database, *, with_concepts: bool = True) -> ChartStore:
    chart = ChartStore(db)
    if with_concepts:
        concepts(chart)
    return chart


async def run(db: Database, llm: FakeFaqLLM, chart: ChartStore) -> list[FAQItem]:
    emitted: list[FAQItem] = []
    await build_faq(
        faq_paper(),
        faq_gateway(db, llm),
        chart,
        db,
        http(db),
        emitted.append,
    )
    return emitted


def mock_all(mock: respx.MockRouter) -> None:
    mock_openreview(mock)
    mock_issues(mock)


async def test_every_source_ends_up_as_a_labelled_item(db: Database) -> None:
    chart = setup(db)
    choose_repo(db, PAPER_ID, repo())
    FindingStore(db).save(PAPER_ID, finding())
    llm = FakeFaqLLM(drop_cluster=1)
    with respx.mock(assert_all_called=False) as mock:
        mock_all(mock)
        items = await run(db, llm, chart)

    by_kind: dict[str, list[FAQItem]] = {}
    for i in items:
        by_kind.setdefault(i.source_kind, []).append(i)
    assert set(by_kind) == {"finding", "reviewer_discussion", "github_issue", "anticipated"}

    # Findings answer themselves, first, with no request.
    assert items[0].source_kind == "finding"
    assert items[0].answer_markdown == "Not exactly. The paper says 0.1; the code sets 0.3."
    assert items[0].anchors_cited == ["eq:1"] and items[0].concept_ids == ["scaled-dot"]

    # Reviewer cluster 0 was kept; cluster 1 had keep=false, so only one reviewer item remains.
    (review,) = by_kind["reviewer_discussion"]
    assert review.source_label == "From reviewer discussion"
    assert review.source_url and review.source_url.startswith("https://openreview.net/forum?id=")
    assert review.question.startswith("Cleaned: ")
    assert review.answer_markdown == "A grounded answer."
    assert "made-up" not in review.anchors_cited  # invented anchors are dropped
    assert review.anchors_cited and all(
        a in {x.id for x in faq_paper().anchors} for a in review.anchors_cited
    )

    labels = {i.source_label for i in by_kind["github_issue"]}
    assert labels <= {"From GitHub issue #7", "From GitHub issue #11"} and labels
    assert all(i.source_url and "/issues/" in i.source_url for i in by_kind["github_issue"])

    for a in by_kind["anticipated"]:
        assert a.source_label == "Anticipated" and a.anchors_cited and a.source_url is None
        assert a.concept_ids  # linked to the concept it was asked about

    # Everything emitted is stored, and nothing is stored that wasn't emitted.
    assert {i.id for i in FaqStore(db).all(PAPER_ID)} == {i.id for i in items}
    # Requests: one answer batch (reviewer + issues) and one anticipated batch.
    assert llm.count("faq_answer") == 1 and llm.count("faq_anticipated") == 1


async def test_a_second_run_adds_nothing_and_costs_nothing(db: Database) -> None:
    chart = setup(db)
    choose_repo(db, PAPER_ID, repo())
    llm = FakeFaqLLM()
    with respx.mock(assert_all_called=False) as mock:
        mock_all(mock)
        first = await run(db, llm, chart)
        calls = len(llm.calls)
        second = await run(db, llm, chart)
    assert [i.id for i in first] == [i.id for i in second]
    assert len(FaqStore(db).all(PAPER_ID)) == len(first)  # same ids replace, never duplicate
    assert len(llm.calls) == calls  # answers came from the gateway cache


async def test_ids_are_stable_across_databases(db: Database, tmp_path: object) -> None:
    from portolan.store.db import open_database

    other = open_database(tmp_path / "other.db")  # type: ignore[operator]
    try:
        ids: list[list[str]] = []
        for database in (db, other):
            chart = setup(database)
            FindingStore(database).save(PAPER_ID, finding())
            with respx.mock(assert_all_called=False) as mock:
                mock_openreview(mock)
                ids.append([i.id for i in await run(database, FakeFaqLLM(), chart)])
        assert ids[0] == ids[1] and ids[0]
    finally:
        other.close()


async def test_a_finding_and_a_similar_issue_become_one_item(db: Database) -> None:
    chart = setup(db, with_concepts=False)
    choose_repo(db, PAPER_ID, repo())
    FindingStore(db).save(PAPER_ID, finding())  # "Does the code use a dropout rate of 0.1 ..."
    issues = [
        {
            "number": 5,
            "title": "Does the code use a dropout rate of 0.1 as in the paper?",
            "body": "x",
            "comments": 3,
            "labels": [],
            "reactions": {"total_count": 0},
            "html_url": "https://github.com/example/transformer/issues/5",
        }
    ]
    llm = FakeFaqLLM()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{OR}/notes/search").mock(return_value=httpx.Response(404))
        mock_issues(mock, issues)
        mock.get(f"{GH}/repos/example/transformer/issues/5/comments").mock(
            return_value=httpx.Response(200, json=[{"body": "Yes.", "author_association": "OWNER"}])
        )
        items = await run(db, llm, chart)
    assert [i.source_kind for i in items] == ["finding"]
    assert llm.calls == []  # the merged cluster is represented by the finding: no request


async def test_findings_survive_without_network_or_llm(db: Database) -> None:
    chart = setup(db, with_concepts=False)  # no concepts → nothing to anticipate
    FindingStore(db).save(PAPER_ID, finding())
    llm = FakeFaqLLM(fail=True)
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(side_effect=httpx.ConnectError("offline"))
        items = await run(db, llm, chart)
    assert [i.source_kind for i in items] == ["finding"] and llm.calls == []


async def test_a_failing_llm_keeps_what_was_collected(db: Database) -> None:
    chart = setup(db)
    choose_repo(db, PAPER_ID, repo())
    FindingStore(db).save(PAPER_ID, finding())
    llm = FakeFaqLLM(fail=True)
    with respx.mock(assert_all_called=False) as mock:
        mock_all(mock)
        items = await run(db, llm, chart)  # must not raise
    assert [i.source_kind for i in items] == ["finding"]
    assert llm.count("faq_anticipated") == 0  # a down provider isn't asked again


async def test_nothing_anywhere_is_fine(db: Database) -> None:
    chart = setup(db, with_concepts=False)
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(404))
        assert await run(db, FakeFaqLLM(), chart) == []


async def test_anticipated_answers_need_a_real_anchor(db: Database) -> None:
    chart = setup(db)
    llm = FakeFaqLLM(invented_concept=0)
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(404))
        items = await run(db, llm, chart)
    assert llm.count("faq_anticipated") == 1
    assert len(items) == 2  # three concepts asked about; the one citing only made-up ids is dropped
    assert all(i.anchors_cited and "made-up" not in i.anchors_cited for i in items)


async def test_budget_and_item_caps(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    chart = setup(db)
    FindingStore(db).save(PAPER_ID, finding())
    llm = FakeFaqLLM()
    monkeypatch.setattr(build, "_budget_allows", lambda _g: False)
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(404))
        items = await run(db, llm, chart)
    assert [i.source_kind for i in items] == ["finding"] and llm.count("faq_anticipated") == 0

    monkeypatch.setattr(build, "_budget_allows", lambda _g: True)
    monkeypatch.setattr(build, "MAX_ITEMS", 2)
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(404))
        capped = await run(db, FakeFaqLLM(), chart)
    assert len(capped) <= 2


def test_link_concepts_prefers_shared_anchors_then_titles(db: Database) -> None:
    chart = setup(db)
    found = chart.concepts(PAPER_ID)
    assert link_concepts(found, "unrelated", ["eq:1"]) == ["positional", "scaled-dot"]
    assert link_concepts(found, "How does multi-head attention work?", []) == ["multi-head"]
    assert link_concepts(found, "nothing", []) == []
