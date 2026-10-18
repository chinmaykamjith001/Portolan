"""Route endpoints with a fake LLM: paste -> chart -> main route -> sub-route."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from portolan.store.routes import RouteStore
from tests.test_api_chart import ARXIV, FakeChartLLM, wait_for
from tests.test_api_chart import client as chart_client  # noqa: F401  (fixture)


class FakeRouteLLM(FakeChartLLM):
    """Chart replies from the parent, plus route_section replies naming every stop it is shown."""

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        if ctx.template_id != "route_section":
            return await super().complete(
                model=model, messages=messages, temperature=temperature, schema=schema, ctx=ctx
            )
        self.calls["route_section"] = self.calls.get("route_section", 0) + 1
        keys = re.findall(r"^\[(s\d+)\] ", messages[-1].content, re.M)
        body: dict[str, Any] = {
            "stops": [
                {
                    "key": k,
                    "title": f"Stop {k}",
                    "role": "definition",
                    "summary": "Says something.",
                    "concept_id": "ghost",
                    "transition_to_next": "and then,",
                }
                for k in keys
            ]
        }
        return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")


@pytest.fixture
def fake() -> FakeRouteLLM:
    return FakeRouteLLM()


@pytest.fixture
def client(chart_client: TestClient) -> Iterator[TestClient]:  # noqa: F811
    yield chart_client


def _paper(client: TestClient) -> str:
    paper_id = client.post("/api/papers", json={"input": ARXIV}).json()["paper_id"]
    wait_for(lambda: client.get(f"/api/papers/{paper_id}/chart/master").status_code == 200)
    return paper_id


def test_main_route_is_generated_once_and_served(client: TestClient, fake: FakeRouteLLM) -> None:
    paper_id = _paper(client)
    url = f"/api/papers/{paper_id}/route/main"
    svc = client.app.state.services  # type: ignore[attr-defined]
    wait_for(lambda: RouteStore(svc.db).get(paper_id, "main"))  # queued after the master layer
    first = fake.calls["route_section"]
    assert first >= 1
    route = client.get(url).json()
    assert route["id"] == "main" and route["paper_id"] == paper_id
    stops = route["stops"]
    assert [s["id"] for s in stops] == [f"stop:{n}" for n in range(1, len(stops) + 1)]
    assert stops[0]["segment_title"] == "§1 Introduction"
    assert stops[0]["title"] == "Stop s1" and stops[0]["concept_id"] is None  # "ghost" was dropped
    assert stops[-1]["transition_to_next"] is None
    assert not any(s["anchor_id"].startswith("sec:A") for s in stops)  # appendix: side road only
    assert any(s["side_roads"] == ["sec:A"] for s in stops)
    client.get(url)
    assert fake.calls["route_section"] == first  # served from the store
    events = [e for e in svc.bus.recent(paper_id) if e.type == "route_ready"]
    assert events and events[-1].data == {"route_id": "main"}


def test_main_route_is_202_until_it_exists(client: TestClient) -> None:
    paper_id = _paper(client)
    svc = client.app.state.services  # type: ignore[attr-defined]
    wait_for(lambda: RouteStore(svc.db).get(paper_id, "main"))
    RouteStore(svc.db).delete_paper(paper_id)
    svc.db.execute("DELETE FROM jobs WHERE job_key = ?", (f"route_main:{paper_id}",))
    svc.queue._jobs.pop(f"route_main:{paper_id}", None)
    url = f"/api/papers/{paper_id}/route/main"
    first = client.get(url)
    assert first.status_code == 202 and first.json() == {"status": "generating"}
    done = wait_for(lambda: (r := client.get(url)).status_code == 200 and r.json())
    assert done["stops"]


def test_unknown_paper_is_404(client: TestClient) -> None:
    r = client.get("/api/papers/nope/route/main")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_sub_route_flow(client: TestClient, fake: FakeRouteLLM) -> None:
    paper_id = _paper(client)
    svc = client.app.state.services  # type: ignore[attr-defined]
    main = wait_for(lambda: RouteStore(svc.db).get(paper_id, "main"))
    stop = next(s for s in main.stops if s.sub_route_id)
    url = f"/api/papers/{paper_id}/route/stops/{stop.id}/sub"
    calls = fake.calls["route_section"]
    assert client.get(url).status_code == 202
    sub = wait_for(lambda: (r := client.get(url)).status_code == 200 and r.json())
    assert sub["id"] == stop.id and sub["parent_stop_id"] == stop.id
    assert [s["id"] for s in sub["stops"]] == [
        f"{stop.id}.{k}" for k in range(1, len(sub["stops"]) + 1)
    ]
    assert fake.calls["route_section"] == calls + 1
    client.get(url)
    assert fake.calls["route_section"] == calls + 1  # stored
    events = [e for e in svc.bus.recent(paper_id) if e.type == "route_ready"]
    assert events[-1].data == {"route_id": stop.id}


def test_sub_route_of_unknown_or_leaf_stop(client: TestClient) -> None:
    paper_id = _paper(client)
    svc = client.app.state.services  # type: ignore[attr-defined]
    main = wait_for(lambda: RouteStore(svc.db).get(paper_id, "main"))
    missing = client.get(f"/api/papers/{paper_id}/route/stops/stop:999/sub")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    leaf = next(s for s in main.stops if not s.sub_route_id)
    r = client.get(f"/api/papers/{paper_id}/route/stops/{leaf.id}/sub")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_sub_route"
