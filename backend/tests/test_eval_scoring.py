from __future__ import annotations

from pathlib import Path

import pytest

from portolan.evalharness.checkpoint import RecordResult
from portolan.evalharness.report import TEST_BANNER, ReportMeta, render_report, write_report
from portolan.evalharness.scoring import (
    DefaultMatcher,
    Tally,
    assign,
    content_words,
    dice,
    same_file,
    score_records,
)
from tests.eval_helpers import finding, gold, record


def done(rid: str, *findings: object) -> RecordResult:
    return RecordResult.model_validate(
        {
            "record_id": rid,
            "engine_version": "v1",
            "status": "done",
            "findings": [f.model_dump(mode="json") for f in findings],  # type: ignore[attr-defined]
        }
    )


def test_path_matching_respects_directory_boundaries() -> None:
    assert same_file("src/model/attn.py", "model/attn.py")
    assert same_file("./a/b.py", "a\\b.py")
    assert not same_file("xattn.py", "attn.py")


def test_content_words_ignore_stopwords_and_simple_endings() -> None:
    assert content_words("The code uses warmup steps") == {"warmup", "step"}
    assert dice({"a", "b"}, {"b", "c"}) == 0.5
    assert dice(set(), {"a"}) == 0.0


def test_matcher_uses_location_within_tolerance_then_text() -> None:
    m = DefaultMatcher()
    g = gold("g", "The scaling factor differs.", file="attn.py", lines=(10, 12))
    near = finding("near", path="src/attn.py", lines=(16, 18), claim="Unrelated words here.")
    far = finding("far", path="src/attn.py", lines=(30, 31), claim="Unrelated words here.")
    other_file = finding("other", path="src/other.py", lines=(10, 12), claim="Unrelated words.")
    near_score = m.score(g, near)
    assert near_score is not None and near_score >= 1.0
    assert m.score(g, far) is None
    assert m.score(g, other_file) is None
    text_only = finding("t", claim="The scaling factor differs from the paper")
    score = m.score(g, text_only)
    assert score is not None and score < 1.0


def test_text_match_needs_enough_shared_words() -> None:
    m = DefaultMatcher()
    g = gold("g", "warmup")
    assert m.score(g, finding("f", claim="warmup")) is None  # one shared word is not enough


def test_assignment_is_one_to_one_best_first() -> None:
    m = DefaultMatcher()
    g1 = gold("g1", "x", file="a.py", lines=(10, 10))
    f_close = finding("close", path="a.py", lines=(10, 10), claim="x")
    f_edge = finding("edge", path="a.py", lines=(15, 15), claim="x")
    matches = assign([g1], [f_edge, f_close], m)
    assert len(matches) == 1 and matches[0].finding_id == "close"  # the better score wins


def test_recall_precision_and_breakdowns_on_a_hand_built_case() -> None:
    g1 = gold("g1", "Scale differs.", file="a.py", lines=(10, 12), category="architecture")
    g2 = gold("g2", "Dropout missing.", file="b.py", lines=(1, 2), category="hyperparameter")
    g3 = gold(
        "g3", "Learning rate warmup steps differ", category="hyperparameter", split="synthetic"
    )
    rec = record("r", [g1, g2, g3])
    findings = [
        finding(
            "f1", path="a.py", lines=(14, 16), method="literal_match", claim_type="architecture"
        ),
        finding("f2", path="b.py", lines=(30, 31), claim="Totally different thing."),
        finding("f3", claim="warmup steps learning rate differ from the paper", method="llm_judge"),
        finding("f4", claim="Extra claim about batch size", claim_type="other"),
        finding("f5", verdict="uncertain"),
        finding("f6", verdict="matches", claim="Scale differs.", path="a.py", lines=(10, 12)),
    ]
    scores = score_records([rec], {"r": done("r", *findings)})

    assert (scores.gold.hit, scores.gold.total) == (2, 3)
    assert scores.recall == pytest.approx(2 / 3)
    assert (scores.predictions.hit, scores.predictions.total) == (2, 4)  # f1 f2 f3 f4
    assert scores.precision == pytest.approx(0.5)
    assert scores.uncertain == 1
    assert scores.matched_by_method == {"literal_match": 1, "llm_judge": 1}
    assert (
        scores.recall_by_category["hyperparameter"].hit,
        scores.recall_by_category["hyperparameter"].total,
    ) == (1, 2)
    assert scores.recall_by_split["synthetic"].rate == 1.0
    assert scores.precision_by_claim_type["other"].rate == 0.0
    [rs] = scores.records
    assert rs.missed == ["g2"] and sorted(rs.false_positives) == ["f2", "f4"]


def test_failed_and_missing_records_are_left_out_of_the_scores() -> None:
    a, b, c = (record(x, [gold("g", "Loss differs here.")]) for x in "abc")
    failed = RecordResult(record_id="b", engine_version="v1", status="failed", error="no repo")
    scores = score_records([a, b, c], {"a": done("a"), "b": failed})
    assert scores.gold.total == 1  # only "a" finished
    assert scores.recall == 0.0 and scores.precision is None  # no predictions: undefined, not 0


def test_empty_tally_has_no_rate() -> None:
    assert Tally().rate is None


def meta(**kw: object) -> ReportMeta:
    base = {
        "command": "portolan eval --data X --limit 2 --split all --seed 7",
        "date": "2026-10-18",
        "seed": 7,
        "limit": 2,
        "split": "all",
        "n_sampled": 2,
        "n_done": 2,
        "n_failed": 0,
        "n_pending": 0,
        "engine_version": "abc123",
        "dataset_path": "X",
        "matcher_name": "location-or-text",
        "matcher_description": "desc",
        "requests_used": 17,
    }
    return ReportMeta(**{**base, **kw})  # type: ignore[arg-type]


def small_run() -> tuple[list, dict, object]:  # type: ignore[type-arg]
    recs = [
        record("a", [gold("g1", "Scale differs from the paper.", file="a.py", lines=(1, 3))]),
        record("b", [gold("g2", "Dropout is missing in the code.")]),
    ]
    results = {
        "a": done("a", finding("fa", path="a.py", lines=(2, 2), claim="Scale", method="static")),
        "b": done("b"),
    }
    return recs, results, score_records(recs, results)


def test_report_contains_the_setup_and_numbers_of_the_run() -> None:
    recs, results, scores = small_run()
    text = render_report(meta(), recs, results, scores)  # type: ignore[arg-type]
    assert "TEST RUN" not in text
    for needle in (
        "`portolan eval --data X --limit 2 --split all --seed 7`",
        "seed 7",
        "`abc123`",
        "Recall: **50.0% (1/2)**",
        "Precision: **100.0% (1/1)**",
        "| static | 1 |",
        "UNVERIFIED",
        "Real requests used (measured): 17",
        "## Examples: found",
        "Dropout is missing in the code.",  # the miss
    ):
        assert needle in text


def test_dry_run_report_is_labeled_and_cannot_be_written(tmp_path: Path) -> None:
    recs, results, scores = small_run()
    text = render_report(meta(test_run=True), recs, results, scores)  # type: ignore[arg-type]
    assert text.startswith(TEST_BANNER)
    with pytest.raises(ValueError, match="not a result"):
        write_report(tmp_path, text, test_run=True)
    assert list(tmp_path.iterdir()) == []


def test_partial_run_is_flagged_and_failures_are_listed() -> None:
    recs, results, _ = small_run()
    results["b"] = RecordResult(
        record_id="b", engine_version="v1", status="failed", error="No repo"
    )
    text = render_report(
        meta(n_done=1, n_failed=1, n_pending=0), recs, results, score_records(recs, results)
    )  # type: ignore[arg-type]
    assert "**Partial.** 1 of 2" in text
    assert "## Records that failed" in text and "| b | No repo |" in text


def test_reports_are_written_per_date_without_overwriting(tmp_path: Path) -> None:
    first = write_report(tmp_path / "reports", "one", day="2026-10-18", test_run=False)
    second = write_report(tmp_path / "reports", "two", day="2026-10-18", test_run=False)
    assert first.name == "2026-10-18.md" and second.name == "2026-10-18-2.md"
    assert first.read_text() == "one"
