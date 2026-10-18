"""Near-duplicate clustering of collected questions."""

from __future__ import annotations

from portolan.faq.cluster import cluster_items
from portolan.faq.sources import RawItem
from portolan.faq.text import rank_texts, tokens


def raw(question: str, kind: str = "github_issue", weight: float = 0.0, text: str = "") -> RawItem:
    return RawItem(
        question=question, source_kind=kind,  # type: ignore[arg-type]
        source_label=kind, source_text=text, weight=weight,
    )  # fmt: skip


def test_near_duplicates_merge_and_the_strongest_source_represents() -> None:
    issue = raw("Why do you divide the scores by sqrt(d_k)?", "github_issue", text="issue reply")
    review = raw("Why are the scores divided by sqrt(d_k)?", "reviewer_discussion", text="rebuttal")
    other = raw("How many encoder layers does the base model have?")
    clusters = cluster_items([issue, other, review])
    assert len(clusters) == 2
    merged = next(c for c in clusters if c.members)
    assert merged.rep is review  # reviewer discussion outranks an issue
    assert merged.members == [issue]
    assert merged.source_text.index("rebuttal") < merged.source_text.index("issue reply")


def test_unrelated_questions_stay_apart_and_order_is_deterministic() -> None:
    items = [raw("What is label smoothing?"), raw("Where is the vocabulary built?")]
    a = cluster_items(items)
    b = cluster_items(list(reversed(items)))
    assert [c.rep.question for c in a] == [c.rep.question for c in b]
    assert len(a) == 2


def test_findings_win_over_everything_else() -> None:
    finding = raw("Does the code use dropout 0.1 as in the paper?", "finding")
    issue = raw("Is dropout 0.1 used as in the paper?", "github_issue", weight=99)
    (cluster,) = cluster_items([issue, finding])
    assert cluster.rep is finding


def test_tokens_fold_plurals_and_drop_stopwords() -> None:
    assert tokens("Why does the model use heads?") == ["model", "us", "head"]
    assert tokens("scores divided") == tokens("score divide")
    assert tokens("Attention is all you need") == ["attention", "all", "ne"]


def test_rank_texts_requires_enough_shared_terms() -> None:
    texts = ["softmax turns scores into weights", "learning rate warmup schedule", "unrelated"]
    assert rank_texts("how does softmax turn scores into weights", texts, 2, min_hits=2) == [0]
    assert rank_texts("warmup", texts, 3) == [1]
    assert rank_texts("zebra", texts, 3) == []
