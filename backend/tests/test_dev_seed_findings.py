"""The demo paper's seeded findings agree with its seeded code and concepts."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from portolan.api.app import build_services, create_app
from portolan.config import Config, ModelsConfig
from portolan.dev.seed import DEMO_PAPER_ID, groundings, seed_demo
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.findings import FindingStore
from portolan.store.papers import load_paper


def seeded(db: Database) -> None:
    seed_demo(db)


def test_seed_writes_one_finding_of_each_interesting_kind(db: Database) -> None:
    seeded(db)
    findings = FindingStore(db).all(DEMO_PAPER_ID)
    assert sorted(f.verdict for f in findings) == [
        "matches", "mismatch", "uncertain", "undocumented_in_paper",
    ]  # fmt: skip
    assert FindingStore(db).run_state(DEMO_PAPER_ID) == "done"
    mismatch = next(f for f in findings if f.verdict == "mismatch")
    assert mismatch.confidence >= 0.7 and mismatch.code_spans and mismatch.evidence
    assert mismatch.concept_ids == ["layer-normalization"]


def test_every_seeded_span_points_at_a_seeded_code_line(db: Database) -> None:
    seeded(db)
    code: dict[str, dict[int, str]] = {}
    for g in groundings():
        for path, lines in g.code.items():
            code.setdefault(path, {}).update(lines)
    for f in FindingStore(db).all(DEMO_PAPER_ID):
        for span in f.code_spans:
            lines = code[span.path]
            assert span.start_line in lines and span.end_line in lines, f.claim_text


def test_findings_are_attached_to_real_concepts_and_set_the_badges(db: Database) -> None:
    seeded(db)
    concepts = ChartStore(db).concepts(DEMO_PAPER_ID)
    doc = load_paper(db, DEMO_PAPER_ID)
    assert doc is not None
    for f in FindingStore(db).all(DEMO_PAPER_ID):
        assert f.concept_ids and all(cid in concepts for cid in f.concept_ids)
        assert f.paper_anchor == "" or doc.anchor(f.paper_anchor) is not None
    counts = {cid: c.issue_count_self for cid, c in concepts.items() if c.issue_count_self}
    assert counts == {"layer-normalization": 1}  # only the confident mismatch is a node warning


def test_seeding_twice_changes_nothing(db: Database) -> None:
    seeded(db)
    first = [f.model_dump() for f in FindingStore(db).all(DEMO_PAPER_ID)]
    seeded(db)
    assert [f.model_dump() for f in FindingStore(db).all(DEMO_PAPER_ID)] == first


def test_the_api_serves_the_seeded_findings(tmp_path: Path) -> None:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    seed_demo(svc.db)
    base = f"/api/papers/{DEMO_PAPER_ID}"
    with TestClient(create_app(config, services=svc)) as client:
        body = client.get(f"{base}/findings").json()
        assert body["state"] == "done" and len(body["findings"]) == 4
        master = client.get(f"{base}/chart/master").json()
        norm = master["concepts"]["layer-normalization"]
        assert norm["issue_count_self"] == 1 and norm["issues_inside"] == 0
        assert client.get(f"{base}/findings").status_code == 200
