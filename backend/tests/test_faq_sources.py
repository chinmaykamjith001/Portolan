"""FAQ sources: OpenReview, GitHub issues and findings. All HTTP is mocked; failures are silent."""

from __future__ import annotations

import httpx
import pytest
import respx

from portolan.faq.sources import (
    finding_items,
    github_issue_items,
    looks_like_question,
    reviewer_items,
    split_questions,
)
from portolan.store.db import Database
from tests.faq_helpers import (
    FORUM,
    GH,
    OR,
    Q1,
    Q2,
    Q3,
    TITLE,
    finding,
    forum_payload,
    http,
    mock_issues,
    mock_openreview,
    repo,
    search_payload,
)

# -- OpenReview ------------------------------------------------------------------------------


@pytest.mark.parametrize("wrapped", [True, False], ids=["v2-values", "v1-plain"])
async def test_reviewer_questions_with_author_replies(db: Database, wrapped: bool) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock_openreview(mock, forum=forum_payload(wrapped=wrapped))
        items = await reviewer_items(http(db), TITLE)
    assert {i.question for i in items} == {Q1, Q2}  # R2 has no reply; "Is this fine?" is too short
    first = items[0]
    assert first.source_kind == "reviewer_discussion"
    assert first.source_label == "From reviewer discussion"
    assert first.source_url == f"https://openreview.net/forum?id={FORUM}"
    assert "unit variance" in first.source_text


async def test_title_must_match_the_forum(db: Database) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock_openreview(mock, search=search_payload("A Completely Different Paper"))
        assert await reviewer_items(http(db), TITLE) == []


async def test_author_reply_nested_under_a_review_counts(db: Database) -> None:
    payload = forum_payload()
    payload["notes"].append(
        {
            "id": "A2", "forum": FORUM, "replyto": "A1",  # a follow-up two levels down
            "invitations": ["Conf/2024/Submission1/-/Official_Comment"],
            "signatures": ["Conf/2024/Submission1/Authors"],
            "content": {"comment": {"value": "Also, see Appendix B for the ablation."}},
        }
    )  # fmt: skip
    with respx.mock(assert_all_called=False) as mock:
        mock_openreview(mock, forum=payload)
        items = await reviewer_items(http(db), TITLE)
    assert all("Appendix B" in i.source_text for i in items)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(404),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"notes": "surprise"}),
        httpx.Response(200, json=[1, 2, 3]),
    ],
    ids=["500", "404", "garbage", "odd-shape", "list"],
)
async def test_openreview_failures_are_silent(db: Database, failure: httpx.Response) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{OR}/notes/search").mock(return_value=failure)
        mock.get(f"{OR}/notes").mock(return_value=failure)
        assert await reviewer_items(http(db), TITLE) == []


async def test_openreview_forum_failure_is_silent(db: Database) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{OR}/notes/search").mock(return_value=httpx.Response(200, json=search_payload()))
        mock.get(f"{OR}/notes").mock(side_effect=httpx.ConnectError("offline"))
        assert await reviewer_items(http(db), TITLE) == []


def test_split_questions_handles_lists_and_short_fragments() -> None:
    text = f"1. {Q1}\n2) {Q2} And one more thing to ask: {Q3}\n* Why?\nNo question here."
    assert split_questions(text) == [Q1, Q2, "And one more thing to ask: " + Q3]


# -- GitHub issues ---------------------------------------------------------------------------


async def test_issue_questions_with_replies(db: Database) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock_issues(mock)
        items = await github_issue_items(http(db), repo())
    assert [i.source_label for i in items] == ["From GitHub issue #7", "From GitHub issue #11"]
    assert items[0].question == "Why is the learning rate warmed up?"
    assert items[0].source_url == "https://github.com/example/transformer/issues/7"
    assert "Warmup avoids early divergence" in items[0].source_text  # the maintainer's reply
    assert all(i.source_kind == "github_issue" for i in items)


async def test_issue_listing_failures_and_other_hosts_are_silent(db: Database) -> None:
    assert await github_issue_items(http(db), None) == []
    assert await github_issue_items(http(db), repo(host="gitlab")) == []
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GH}/repos/example/transformer/issues").mock(return_value=httpx.Response(403))
        assert await github_issue_items(http(db), repo()) == []
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GH}/repos/example/transformer/issues").mock(
            side_effect=httpx.ConnectError("offline")
        )
        assert await github_issue_items(http(db), repo()) == []


async def test_token_goes_only_to_github(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    with respx.mock(assert_all_called=False) as mock:
        mock_issues(mock)
        await github_issue_items(http(db), repo())
        calls = [c.request for c in mock.calls]
    assert calls and all(r.headers.get("authorization") == "Bearer t0ken" for r in calls)
    assert all(r.url.host == "api.github.com" for r in calls)


@pytest.mark.parametrize(
    ("title", "labels", "expected"),
    [
        ("Why is X used?", [], True),
        ("How to train on 2 GPUs", [], True),
        ("Does it support fp16", [], True),
        ("Add fp16 support", [], False),
        ("Add fp16 support", ["Question"], True),
        ("Bug in loss", [], False),
    ],
)
def test_question_heuristic(title: str, labels: list[str], expected: bool) -> None:
    assert looks_like_question(title, "", labels) is expected


# -- Findings --------------------------------------------------------------------------------


def test_findings_become_self_answering_questions() -> None:
    items = finding_items(
        [finding("mismatch", "a"), finding("undocumented_in_paper", "b"), finding("matches", "c")]
    )
    assert len(items) == 2  # matches and uncertain verdicts are not FAQ material
    mismatch, extra = items
    assert mismatch.question == "Does the code use a dropout rate of 0.1 as in the paper?"
    assert mismatch.answer == "Not exactly. The paper says 0.1; the code sets 0.3."
    assert mismatch.source_kind == "finding" and mismatch.source_label == "Finding"
    assert mismatch.anchors == ["eq:1"] and mismatch.concept_ids == ["scaled-dot"]
    assert extra.question.startswith("Does the code do something the paper doesn't describe")
    assert extra.answer is not None and extra.answer.startswith("Yes.")
