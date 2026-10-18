"""The demo seed (used by the Playwright tests) serves a full Chart without network or LLM calls."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel
from typer.testing import CliRunner

from portolan.api.app import build_services, create_app
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
