"""The demo seed (used by the Playwright tests) serves a full Chart without network or LLM calls."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel
from typer.testing import CliRunner

from portolan.api.app import build_services, create_app
from portolan.api.services import Services
from portolan.cli import app as cli
from portolan.config import Config, ModelsConfig
from portolan.dev.seed import DEMO_PAPER_ID, LAYERS, seed_demo
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse


class NoLLM:
    name = "no-llm"
    uses_quota = False
    calls = 0

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        NoLLM.calls += 1
        raise AssertionError(f"unexpected LLM call: {ctx.template_id}")


def test_seeded_demo_serves_every_layer_without_llm(tmp_path: Path) -> None:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    assert seed_demo(svc.db) == DEMO_PAPER_ID
    seed_demo(svc.db)  # idempotent
    svc.gateway = Gateway(config, svc.db, provider=NoLLM(), on_event=svc.on_gateway_event)
    base = f"/api/papers/{DEMO_PAPER_ID}"

    with TestClient(create_app(config, services=svc)) as client:
        info = client.get(base).json()
        assert info["status"] == "ready" and info["chart_ready"] is True
        master = client.get(f"{base}/chart/master").json()
        assert len(master["layer"]["concepts"]) == 8
        assert {c["grounding"] for c in master["concepts"].values()} == {
            "in_code", "in_library", "concept_only", "shown_by_running", "unknown",
        }  # fmt: skip
        for layer_id in LAYERS:
            if layer_id == "master":
                continue
            layer = client.get(f"{base}/chart/layers/{layer_id}")
            assert layer.status_code == 200
            for link in layer.json()["layer"]["links"]:
                edge = client.get(f"{base}/links/{link['id']}/explanation")
                assert edge.status_code == 200 and edge.json()["headline"]
        for cid in master["layer"]["concepts"]:
            concept = client.get(f"{base}/concepts/{cid}").json()
            assert concept["details"]["explanation"] is True
            assert concept["concept"]["has_layer"] == (cid in LAYERS)
            explanation = client.get(f"{base}/concepts/{cid}/explanation")
            assert explanation.status_code == 200
        # Opening a layer queues its frontier; with every unexpanded concept a leaf, it's a no-op.
        sql = "SELECT kind, state FROM jobs WHERE paper_id = ?"
        deadline = time.monotonic() + 10
        jobs = svc.db.fetchall(sql, (DEMO_PAPER_ID,))
        while time.monotonic() < deadline:
            jobs = svc.db.fetchall(sql, (DEMO_PAPER_ID,))
            if jobs and all(row["state"] in ("done", "failed") for row in jobs):
                break
            time.sleep(0.05)
        assert jobs and all(row["state"] == "done" for row in jobs)
    assert NoLLM.calls == 0


def test_seed_demo_command(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["seed-demo"])
    assert result.exit_code == 0, result.output
    assert DEMO_PAPER_ID in result.output and "?paper=demo:toyformer" in result.output


def _seeded_client(tmp_path: Path) -> tuple[Services, TestClient]:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    seed_demo(svc.db)
    svc.gateway = Gateway(config, svc.db, provider=NoLLM(), on_event=svc.on_gateway_event)
    return svc, TestClient(create_app(config, services=svc))


def test_seeded_details_are_valid_and_served_without_llm(tmp_path: Path) -> None:
    from portolan.dev.seed import CONCEPTS, examples, graphs, groundings
    from portolan.models.detail import CodeGrounding, Example, GraphSpec

    svc, client = _seeded_client(tmp_path)
    base = f"/api/papers/{DEMO_PAPER_ID}"
    NoLLM.calls = 0
    with client:
        info = client.get(base).json()
        assert info["repo"]["id"] == "github:ada-example/toyformer"
        assert "Official code found" in info["repo_status"]["message"]

        # Code: every note points at a real line of a real span, and every span has its text.
        seeded = {g.concept_id: g for g in groundings()}
        assert {g.grounding for g in seeded.values()} >= {"in_code", "in_library", "concept_only"}
        kinds = {n.kind for g in seeded.values() for n in g.notes}
        assert kinds == {"implements", "detail_not_in_paper", "mismatch", "context"}
        for cid, g in seeded.items():
            body = client.get(f"{base}/concepts/{cid}/code")
            assert body.status_code == 200, cid
            got = CodeGrounding.model_validate(body.json())
            for note in got.notes:
                spans = [s for s in got.spans if s.start_line <= note.line <= s.end_line]
                assert spans, (cid, note)
                assert note.line in got.code[spans[0].path], (cid, note)
            for span in got.spans:
                assert set(got.code[span.path]) >= set(range(span.start_line, span.end_line + 1))
                assert span.path in got.links
            assert got.grounding == g.grounding
        for cid in (c.id for c in CONCEPTS.values() if c.grounding == "concept_only"):
            assert cid in seeded and seeded[cid].spans == []

        # Examples: tables are rectangular, two variants for softmax, a repo-run label exists.
        by_concept: dict[str, list[Example]] = {}
        for e in examples():
            by_concept.setdefault(e.concept_id, []).append(e)
            assert all(len(r) == len(e.columns) for r in e.rows)
            got_e = Example.model_validate(
                client.get(f"{base}/concepts/{e.concept_id}/example?variant={e.variant}").json()
            )
            assert got_e.rows == e.rows
        assert len(by_concept["softmax"]) == 2
        assert {e.source_label for es in by_concept.values() for e in es} >= {
            "paper_notation", "from_repo_run",
        }  # fmt: skip
        # The numbers come from the computation, e.g. the three softmax weights sum to ~1.
        weights = [float(r[1]) for r in by_concept["softmax"][0].rows]
        assert abs(sum(weights) - 1) < 0.01
        assert by_concept["softmax"][1].rows[0][1:] == ["nan", "nan"]

        # Graphs: four templates have data; every other concept explicitly has none.
        with_graph = {g.concept_id: g for g in graphs()}
        assert {g.template for g in with_graph.values()} == {
            "bar", "heatmap", "before_after", "line",
        }  # fmt: skip
        for cid in CONCEPTS:
            body = client.get(f"{base}/concepts/{cid}/graph")
            assert body.status_code == 200, cid
            if cid in with_graph:
                assert GraphSpec.model_validate(body.json()).template == with_graph[cid].template
            else:
                assert body.json() == {"template": "none"}
            flag = client.get(f"{base}/concepts/{cid}").json()["details"]["graph"]
            assert flag == (cid in with_graph)

        details = client.get(f"{base}/concepts/softmax").json()["details"]
        assert details == {"explanation": True, "code": True, "example": True, "graph": True}
        # Nothing was queued: no generation job exists for any of these requests.
        rows = svc.db.fetchall(
            "SELECT kind FROM jobs WHERE paper_id = ? AND kind IN ('ground', 'example', 'graph')",
            (DEMO_PAPER_ID,),
        )
        assert rows == []
    assert NoLLM.calls == 0
