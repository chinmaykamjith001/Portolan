"""GET /findings and the `issues_inside` field on concepts (spec §12, §10.5.4)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from portolan.api.app import build_services, create_app
from portolan.config import Config, ModelsConfig
from portolan.models.concept import MASTER, Concept, Layer
from portolan.store.papers import save_paper
from portolan.verify.engine import verify_paper
from tests.verify_helpers import (
    PAPER_ID,
    ScriptedVerify,
    extract_reply,
    full_script,
    gateway,
    link_repo,
    paper,
)

BASE = f"/api/papers/{PAPER_ID}"


def concept(
    cid: str, title: str, depth: int, anchors: list[str], *, has_layer: bool = False
) -> Concept:
    return Concept(
        id=cid, canonical_name=cid, title=title, gist=title, depth=depth, anchors=anchors,
        has_layer=has_layer, gen_state="detailed",
    )  # fmt: skip


def test_findings_endpoint_and_issue_rollup(tmp_path: Path) -> None:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    save_paper(svc.db, paper())

    # decoder-stack contains decoder-block (anchored on the paragraph about the decoder depth).
    for c in (
        concept("toy", "Toy", 0, ["sec:3"], has_layer=True),
        concept("decoder-stack", "Decoder stack", 1, ["sec:3.1"], has_layer=True),
        concept("decoder-block", "Decoder block", 2, ["para:3.1:1"]),
        concept("attention", "Scaled attention", 1, ["sec:3.2"]),
        concept("training", "Training setup", 1, ["sec:5"]),
    ):
        svc.chart.upsert_concept(PAPER_ID, c)
    for layer_id, center, members in (
        (MASTER, "toy", ["decoder-stack", "attention", "training"]),
        ("decoder-stack", "decoder-stack", ["decoder-block"]),
    ):
        svc.chart.save_layer(
            Layer(id=layer_id, paper_id=PAPER_ID, center=center, concepts=members, links=[],
                  gen_state="detailed")
        )  # fmt: skip
    link_repo(svc.db)

    with TestClient(create_app(config, services=svc)) as client:
        before = client.get(f"{BASE}/findings").json()
        assert before == {"findings": [], "state": "none"}
        assert client.get("/api/papers/nope/findings").status_code == 404

        import asyncio

        asyncio.run(
            verify_paper(
                paper(), gateway(svc.db, full_script(paper())), svc.chart, svc.db, lambda _f: None
            )
        )

        body = client.get(f"{BASE}/findings").json()
        assert body["state"] == "done" and len(body["findings"]) == 9
        mismatch = next(f for f in body["findings"] if f["verdict"] == "mismatch")
        assert {"id", "claim_text", "paper_anchor", "code_spans", "evidence", "concept_ids"} <= set(
            mismatch
        )

        master = client.get(f"{BASE}/chart/master").json()["concepts"]
        # decoder depth is a confident mismatch on the block; the stack above it counts it inside.
        assert master["decoder-stack"]["issue_count_self"] == 0
        assert master["decoder-stack"]["issues_inside"] == 1
        assert (
            master["attention"]["issue_count_self"] == 1
            and master["attention"]["issues_inside"] == 0
        )
        assert master["training"]["issue_count_self"] == 1
        child = client.get(f"{BASE}/chart/layers/decoder-stack").json()["concepts"]
        assert child["decoder-block"]["issue_count_self"] == 1
        assert child["decoder-block"]["issues_inside"] == 0

        detail = client.get(f"{BASE}/concepts/decoder-stack").json()
        assert detail["issues_inside"] == 1
        assert client.get(f"{BASE}/concepts/decoder-block").json()["issues_inside"] == 0


def test_findings_endpoint_reports_a_failed_run(tmp_path: Path) -> None:
    import asyncio

    import pytest

    from portolan.llm.types import LLMError

    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    save_paper(svc.db, paper())
    link_repo(svc.db)
    provider = ScriptedVerify(
        claims_extract=[extract_reply(paper(), ("dropout",))],
        reverse_scan=[LLMError("down")],
    )
    with TestClient(create_app(config, services=svc)) as client:
        with pytest.raises(LLMError):
            asyncio.run(
                verify_paper(paper(), gateway(svc.db, provider), svc.chart, svc.db, lambda _f: None)
            )
        body = client.get(f"{BASE}/findings").json()
        assert body["state"] == "failed" and len(body["findings"]) == 1  # kept what it had
