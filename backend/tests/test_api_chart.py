"""API flow for M4 with a fake LLM: paste → ingest → master layer → frontier → jump in."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.api.app import build_services, create_app
from portolan.config import Config, ModelsConfig
from portolan.ingest.arxiv import API_URL
from portolan.ingest.http import PoliteClient
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from tests.ingest_helpers import FIXTURES
from tests.test_ingest_fetch import atom

ARXIV = "2101.00001"


class FakeChartLLM:
    """Valid chart replies for any input; counts calls per template."""

    name = "fake"
    uses_quota = False

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.counter = 0

    def _concepts(self, prefix: str, n: int, leaf: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _ in range(n):
            self.counter += 1
            out.append({
                "key": f"{prefix}-{self.counter}",
                "title": f"Idea {self.counter}",
                "gist": f"Gist of idea {self.counter}.",
                "anchors": ["sec:2.1", "not-a-real-anchor"],
                "is_first_principle": leaf,
                "grounding_guess": "concept_only",
            })  # fmt: skip
        return out

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        self.calls[ctx.template_id] = self.calls.get(ctx.template_id, 0) + 1
        if ctx.template_id == "master_layer":
            concepts = self._concepts("m", 12, leaf=False)
            body: dict[str, Any] = {
                "center": {"title": "Toy Attention", "gist": "A toy model.", "anchors": ["sec:1"]},
                "concepts": [*concepts, {**concepts[0], "key": "dup", "title": "Method"}],
                "links": [
                    {"source": "center", "target": c["key"], "label": "uses", "kind": "uses"}
                    for c in concepts
                ]
                + [
                    {"source": "m-1", "target": "m-2", "label": "feeds", "kind": "enables"},
                    {"source": "m-1", "target": "ghost", "label": "breaks", "kind": "other"},
                ],
            }
        elif ctx.template_id == "skeleton_batch":
            user = messages[-1].content
            keys = json.loads("[" + re.search(r"in order: (.*)\. Return", user).group(1) + "]")  # type: ignore[union-attr]
            body = {
                "layers": [
                    {
                        "center_key": key,
                        "concepts": self._concepts("s", 4, leaf=self.counter > 60),
                        "links": [
                            {
                                "source": "center",
                                "target": f"s-{self.counter}",
                                "label": "contains",
                                "kind": "part_of",
                            }
                        ],
                    }
                    for key in keys
                ]
            }
        else:
            body = {"same": False, "reason": "Different."}
        return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")


@pytest.fixture
def fake() -> FakeChartLLM:
    return FakeChartLLM()


@pytest.fixture
def client(tmp_path: Path, fake: FakeChartLLM) -> Iterator[TestClient]:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    svc.gateway = Gateway(config, svc.db, provider=fake, on_event=svc.on_gateway_event)
    hosts = ("export.arxiv.org", "arxiv.org", "huggingface.co", "api.github.com")
    no_wait = {h: 0.0 for h in hosts}
    svc.http = PoliteClient(svc.db, spacing=no_wait)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(API_URL).mock(
            return_value=httpx.Response(200, content=atom((f"{ARXIV}v1", "Toy Attention", ["Ada"])))
        )
        mock.get(f"https://arxiv.org/html/{ARXIV}v1").mock(
            return_value=httpx.Response(200, content=(FIXTURES / "tiny_latexml.html").read_bytes())
        )
        mock.route(host="huggingface.co").mock(return_value=httpx.Response(404))
        mock.route(host="api.github.com").mock(return_value=httpx.Response(404))
        mock.route(host="example.org").mock(return_value=httpx.Response(404))
        with TestClient(create_app(config, services=svc)) as c:
            yield c


def wait_for(fn: Any, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")  # see job errors via services.queue


def test_paste_to_master_layer_and_frontier(client: TestClient, fake: FakeChartLLM) -> None:
    created = client.post("/api/papers", json={"input": f"https://arxiv.org/abs/{ARXIV}"})
    assert created.status_code == 202
    paper_id = created.json()["paper_id"]
    assert paper_id == f"arxiv:{ARXIV}v1"

    master = wait_for(
        lambda: (
            (r := client.get(f"/api/papers/{paper_id}/chart/master")).status_code == 200
            and r.json()
        )
    )
    layer, concepts = master["layer"], master["concepts"]
    assert layer["id"] == "master" and len(layer["concepts"]) == 12  # "Method" dropped as a section
    assert concepts[layer["center"]]["title"] == "Toy Attention"
    first = concepts[layer["concepts"][0]]
    assert first["anchors"] == ["sec:2.1"]  # invented anchor ids are removed
    assert not any(link["target"] == "ghost" for link in layer["links"])
    assert any(link["label"] == "feeds" for link in layer["links"])

    # The frontier fills two levels below the master layer in the background.
    child_id = layer["concepts"][0]
    child = wait_for(
        lambda: (
            (r := client.get(f"/api/papers/{paper_id}/chart/layers/{child_id}")).status_code == 200
            and r.json()
        )
    )
    grandchild_id = child["layer"]["concepts"][0]
    wait_for(
        lambda: (
            client.get(f"/api/papers/{paper_id}/chart/layers/{grandchild_id}").status_code == 200
        )
    )
    assert fake.calls["master_layer"] == 1
    # 12 master concepts in batches of 6, then 48 children in batches of 6: 2 + 8 skeleton calls.
    wait_for(lambda: fake.calls.get("skeleton_batch", 0) >= 10)

    info = client.get(f"/api/papers/{paper_id}").json()
    assert info["status"] == "ready" and info["chart_ready"] is True
    concept = client.get(f"/api/papers/{paper_id}/concepts/{child_id}").json()
    assert concept["concept"]["has_layer"] is True
    assert "master" in concept["also_appears_in"]
    assert concept["issues_inside"] == 0


def test_errors_are_plain(client: TestClient) -> None:
    bad = client.post("/api/papers", json={"input": "https://example.com/x"})
    assert bad.status_code == 400 and "arXiv" in bad.json()["error"]["message"]
    missing = client.get("/api/papers/arxiv:9999.99999v1/chart/master")
    assert missing.status_code == 404
    malformed = client.post("/api/papers", json={})
    assert malformed.status_code == 422 and malformed.json()["error"]["code"] == "bad_request"


def test_sse_stream_replays_progress(client: TestClient) -> None:
    paper_id = client.post("/api/papers", json={"input": ARXIV}).json()["paper_id"]
    wait_for(lambda: client.get(f"/api/papers/{paper_id}/chart/master").status_code == 200)
    svc = client.app.state.services  # type: ignore[attr-defined]
    types = [e.type for e in svc.bus.recent(paper_id)]
    assert "progress" in types and "layer_ready" in types
