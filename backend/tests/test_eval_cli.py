from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from portolan.cli import app
from portolan.config import Config
from portolan.evalharness import commands
from portolan.evalharness.real import RealEngine, candidate_from_url
from portolan.evalharness.runner import BudgetExhaustedStop, EngineOutput, EvalError
from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import IngestReport
from portolan.ingest.resolver import ResolveResult
from portolan.llm.gateway import Gateway
from portolan.models.finding import Finding
from portolan.models.paper import PaperDoc, PaperRef
from portolan.models.repo import RepoCandidate
from portolan.store.db import Database
from portolan.store.findings import FindingStore
from tests.eval_helpers import finding, native_rows, record, write_jsonl

runner = CliRunner()


def invoke(*args: str, input: str | None = None) -> Any:
    return runner.invoke(app, ["eval", *args], input=input)


def test_dry_run_prints_a_labeled_report_and_writes_nothing(tmp_path: Path) -> None:
    result = invoke("--dry-run")
    assert result.exit_code == 0, result.output
    assert "TEST RUN — not a result" in result.output
    assert "Dry run: the fake engine makes no requests" in result.output
    assert not (tmp_path / "eval").exists()  # neither reports nor checkpoints in the repo root


def test_inspect_shows_how_fields_are_read(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "d.jsonl", native_rows(1))
    result = invoke("--inspect", str(path))
    assert result.exit_code == 0
    assert "UNVERIFIED" in result.output and "detected   repo_url" in result.output


def test_inspect_of_a_missing_path_is_a_plain_error(tmp_path: Path) -> None:
    result = invoke("--inspect", str(tmp_path / "nope"))
    assert result.exit_code == 1 and "doesn't exist" in result.output


def test_a_real_run_needs_data(tmp_path: Path) -> None:
    result = invoke()
    assert result.exit_code == 1 and "UKPLab/scicoqa" in result.output


def test_a_real_run_refuses_the_mock_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTOLAN_LLM_PROVIDER", "mock")
    data = write_jsonl(tmp_path / "d.jsonl", native_rows(2))
    result = invoke("--data", str(data))
    assert result.exit_code == 1 and "needs the real provider" in result.output


class Scripted:
    version = "scripted"

    def __init__(self, stop_after: int | None = None) -> None:
        self.stop_after = stop_after
        self.calls = 0

    async def check(self, rec: object, /) -> EngineOutput:
        self.calls += 1
        if self.stop_after is not None and self.calls > self.stop_after:
            raise BudgetExhaustedStop()
        return EngineOutput(findings=[finding(f"f{self.calls}", claim="differs")], requests_used=4)


def use_engine(monkeypatch: pytest.MonkeyPatch, engine: Scripted) -> None:
    monkeypatch.setattr(commands, "_real_engine", lambda _c, _d: (engine, None, 10))


def test_real_run_estimates_asks_then_writes_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_engine(monkeypatch, Scripted())
    data = write_jsonl(tmp_path / "d.jsonl", native_rows(3))
    out, reports = tmp_path / "runs", tmp_path / "reports"
    args = ("--data", str(data), "--out", str(out), "--report-dir", str(reports), "--limit", "3")

    declined = invoke(*args, input="n\n")  # 3 records x ~15 requests is above the 30 threshold
    assert declined.exit_code == 1 and "roughly 45 real requests" in declined.output
    assert not reports.exists()

    result = invoke(*args, input="y\n")
    assert result.exit_code == 0, result.output
    [report] = list(reports.iterdir())
    text = report.read_text(encoding="utf-8")
    assert "TEST RUN" not in text and "Recall:" in text and "scripted" in text
    assert f"Report written to {report}" in result.output


def test_yes_skips_the_question_and_resume_skips_finished_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Scripted()
    use_engine(monkeypatch, engine)
    data = write_jsonl(tmp_path / "d.jsonl", native_rows(3))
    args = ("--data", str(data), "--out", str(tmp_path / "r"), "--report-dir", str(tmp_path / "p"))
    assert invoke(*args, "--yes").exit_code == 0 and engine.calls == 3
    again = invoke(*args, "--yes")
    assert again.exit_code == 0 and engine.calls == 3  # nothing re-run
    assert "Nothing left to run" in again.output and "already done 3" in again.output
    assert invoke(*args, "--yes", "--fresh").exit_code == 0 and engine.calls == 6


def test_budget_stop_saves_progress_and_reports_what_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_engine(monkeypatch, Scripted(stop_after=2))
    data = write_jsonl(tmp_path / "d.jsonl", native_rows(4))
    result = invoke(
        "--data",
        str(data),
        "--yes",
        "--out",
        str(tmp_path / "r"),
        "--report-dir",
        str(tmp_path / "p"),
    )
    assert result.exit_code == 0
    assert "Stopped: today's free-tier requests are used up. 2 record(s) are left" in result.output
    [report] = (tmp_path / "p").iterdir()
    assert "**Partial.** 2 of 4" in report.read_text(encoding="utf-8")


def test_a_run_where_nothing_finishes_writes_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_engine(monkeypatch, Scripted(stop_after=0))
    data = write_jsonl(tmp_path / "d.jsonl", native_rows(2))
    result = invoke(
        "--data",
        str(data),
        "--yes",
        "--out",
        str(tmp_path / "r"),
        "--report-dir",
        str(tmp_path / "p"),
    )
    assert result.exit_code == 1 and "nothing to score" in result.output
    assert not (tmp_path / "p").exists()


# -- the real engine, with every external step replaced ----------------------------------------


def doc() -> PaperDoc:
    return PaperDoc(
        paper_id="arxiv:2001.00001v1",
        ref=PaperRef(arxiv_id="2001.00001", version="v1"),
        title="T",
        authors=[],
        abstract="",
        anchors=[],
    )


class Harness:
    def __init__(
        self, db: Database, *, index_ok: bool = True, resolved: ResolveResult | None = None
    ) -> None:
        self.db = db
        self.requests = 0
        self.index_ok = index_ok
        self.resolved = resolved or ResolveResult(ref=PaperRef(arxiv_id="2001.00001", version="v1"))
        self.verified = 0
        self.indexed: list[str] = []

    async def resolve(self, raw: str, client: object) -> ResolveResult:
        return self.resolved

    async def ingest(self, ref: PaperRef, client: object, db: object) -> IngestReport:
        return IngestReport(doc=doc())

    async def index(self, config: Config, db: Database, paper_id: str, repo: RepoCandidate) -> bool:
        self.indexed.append(repo.id)
        db.execute(
            "INSERT OR REPLACE INTO code_indexes"
            " (repo_id, commit_sha, root, index_json, created_at)"
            " VALUES (?, 'deadbeefcafe', '/x', '{}', 'now')",
            (repo.id,),
        )
        return self.index_ok

    async def verify(
        self, d: PaperDoc, gw: object, chart: object, db: Database, on: object
    ) -> None:
        self.verified += 1
        self.requests += 5
        FindingStore(db).save(d.paper_id, finding("new", claim="found"))

    def engine(self, config: Config) -> RealEngine:
        return RealEngine(
            config,
            self.db,
            cast(Gateway, object()),
            cast(PoliteClient, object()),
            count_requests=lambda: self.requests,
            resolve_fn=self.resolve,
            ingest_fn=self.ingest,
            index_fn=self.index,
            verify_fn=self.verify,
        )


async def test_real_engine_runs_the_pipeline_and_returns_only_this_runs_findings(
    db: Database, config: Config
) -> None:
    h = Harness(db)
    FindingStore(db).save("arxiv:2001.00001v1", cast(Finding, finding("stale")))
    rec = record("r", [])
    rec = rec.model_copy(
        update={"repo_url": "https://github.com/Owner/Repo.git/", "repo_commit": "0123abcd"}
    )
    out = await h.engine(config).check(rec)
    assert [f.id for f in out.findings] == ["new"]  # earlier findings were cleared first
    assert out.requests_used == 5 and h.indexed == ["github:Owner/Repo"]
    assert out.note is not None and "0123abcd" in out.note and "deadbeefcafe" in out.note


async def test_real_engine_failures_are_plain_sentences(db: Database, config: Config) -> None:
    rec = record("r", [])
    with pytest.raises(EvalError, match="Couldn't download the code"):
        await Harness(db, index_ok=False).engine(config).check(rec)
    with pytest.raises(EvalError, match="isn't a GitHub or GitLab"):
        await (
            Harness(db)
            .engine(config)
            .check(rec.model_copy(update={"repo_url": "https://example.com/x"}))
        )
    bad = ResolveResult(error="Couldn't find that paper.")
    with pytest.raises(EvalError, match="Couldn't find that paper"):
        await Harness(db, resolved=bad).engine(config).check(rec)
    ambiguous = ResolveResult()
    with pytest.raises(EvalError, match="matches several papers"):
        await Harness(db, resolved=ambiguous).engine(config).check(rec)


def test_repo_urls_become_candidates() -> None:
    c = candidate_from_url("https://github.com/Owner/Repo/tree/main/src")
    assert (c.id, c.url, c.host) == ("github:Owner/Repo", "https://github.com/Owner/Repo", "github")
    assert json.loads(c.model_dump_json())["source"] == "user"
