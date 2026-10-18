"""API flow for worked examples and graphs with a fake LLM and a fake sandbox."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.detail import example as example_module
from portolan.detail import graph as graph_module
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from tests.detail_helpers import FakeSandbox, good
from tests.test_api_chart import FakeChartLLM, wait_for
from tests.test_api_chart import client as chart_client  # noqa: F401  (fixture)
from tests.test_api_details import _master  # pyright: ignore[reportPrivateUsage]

TABLE = {"columns": ["score", "weight"], "rows": [["2.0", "0.659"], ["1.0", "0.242"]]}
BARS = {"x": ["a", "b"], "series": [{"name": "w", "y": [1, 2]}]}
PROGRAM = "import json\nprint(json.dumps(1))"


class FakeProgramLLM(FakeChartLLM):
    """Chart replies from the parent, plus example and graph replies."""

    def __init__(self) -> None:
        super().__init__()
        self.no_graph_for: set[str] = set()
        self.prompts: list[tuple[str, str]] = []

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        user = messages[-1].content
        if ctx.template_id == "example_program":
            self.calls["example_program"] = self.calls.get("example_program", 0) + 1
            self.prompts.append(("example", user))
            body: dict[str, Any] = {
                "caption": "A small case",
                "source_label": "generic",
                "program": PROGRAM + f"\n# {self.calls['example_program']}",
                "note": "The first weight is 0.77.",  # never computed by the run
            }
            return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")
        if ctx.template_id == "graph_program":
            self.calls["graph_program"] = self.calls.get("graph_program", 0) + 1
            skip = any(f"Concept: {title}\n" in user for title in self.no_graph_for)
            body = (
                {"template": "none"}
                if skip
                else {"template": "bar", "caption": "Weights", "program": PROGRAM, "note": None}
            )
            return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")
        return await super().complete(
            model=model, messages=messages, temperature=temperature, schema=schema, ctx=ctx
        )


@pytest.fixture
def fake() -> FakeProgramLLM:
    return FakeProgramLLM()


@pytest.fixture
def client(chart_client: TestClient) -> Iterator[TestClient]:  # noqa: F811
    yield chart_client


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    """Examples get a table and graphs get bar data, whatever program was written."""
    box = FakeSandbox([])

    def fake_run(output: object) -> Any:
        async def run(source: str, **_: Any) -> Any:
            box.sources.append(source)
            return good(output)

        return run

    monkeypatch.setattr(example_module, "run_program", fake_run(TABLE))
    monkeypatch.setattr(graph_module, "run_program", fake_run(BARS))
    return box


def test_example_flow(client: TestClient, fake: FakeProgramLLM) -> None:
    paper_id, master = _master(client)
    cid = master["layer"]["concepts"][0]
    url = f"/api/papers/{paper_id}/concepts/{cid}/example"
    first = client.get(url)
    assert first.status_code == 202 and first.json() == {"status": "generating"}
    done = wait_for(lambda: (r := client.get(url)).status_code == 200 and r.json())
    assert (done["columns"], done["rows"]) == (TABLE["columns"], TABLE["rows"])
    assert done["variant"] == 0 and done["source_label"] == "generic"
    assert done["note"] is None  # "0.77" was typed by the LLM, not produced by the run
    assert client.get(f"/api/papers/{paper_id}/concepts/{cid}").json()["details"]["example"]
    client.get(url)
    assert fake.calls["example_program"] == 1  # stored: a second GET doesn't regenerate
    svc = client.app.state.services  # type: ignore[attr-defined]
    events = [e for e in svc.bus.recent(paper_id) if e.type == "detail_ready"]
    assert events[-1].data == {"concept_id": cid, "part": "example", "variant": 0}


def test_another_example_sees_the_first(client: TestClient, fake: FakeProgramLLM) -> None:
    paper_id, master = _master(client)
    cid = master["layer"]["concepts"][0]
    base = f"/api/papers/{paper_id}/concepts/{cid}/example"
    wait_for(lambda: (r := client.get(base)).status_code == 200 and r.json())
    second = wait_for(
        lambda: (r := client.get(f"{base}?variant=1")).status_code == 200 and r.json()
    )
    assert second["variant"] == 1
    kind, user = fake.prompts[-1]
    assert (
        kind == "example" and "DIFFERENT case" in user and "Earlier example 1: A small case" in user
    )
    assert client.get(base).json()["variant"] == 0  # variants are stored separately


def test_example_errors(client: TestClient) -> None:
    paper_id, master = _master(client)
    cid = master["layer"]["concepts"][0]
    assert client.get(f"/api/papers/{paper_id}/concepts/nope/example").status_code == 404
    bad = client.get(f"/api/papers/{paper_id}/concepts/{cid}/example?variant=99")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_variant"
    assert (
        client.get(f"/api/papers/{paper_id}/concepts/{cid}/example?variant=-1").status_code == 400
    )


def test_a_graph_appears_only_when_the_model_chose_one(
    client: TestClient, fake: FakeProgramLLM
) -> None:
    paper_id, master = _master(client)
    with_graph, without_graph = master["layer"]["concepts"][:2]
    fake.no_graph_for = {master["concepts"][without_graph]["title"]}

    url = f"/api/papers/{paper_id}/concepts/{with_graph}/graph"
    assert client.get(url).status_code == 202
    spec = wait_for(lambda: (r := client.get(url)).status_code == 200 and r.json())
    assert spec["template"] == "bar" and spec["caption"] == "Weights"
    assert spec["data"]["series"] == [{"name": "w", "y": [1.0, 2.0]}]
    assert client.get(f"/api/papers/{paper_id}/concepts/{with_graph}").json()["details"]["graph"]

    none_url = f"/api/papers/{paper_id}/concepts/{without_graph}/graph"
    assert client.get(none_url).status_code == 202
    assert wait_for(lambda: (r := client.get(none_url)).status_code == 200 and r.json()) == {
        "template": "none"
    }
    assert not client.get(f"/api/papers/{paper_id}/concepts/{without_graph}").json()["details"][
        "graph"
    ]
    before = fake.calls["graph_program"]
    client.get(none_url)
    assert fake.calls["graph_program"] == before  # "no graph" is remembered, not re-asked
    assert client.get(f"/api/papers/{paper_id}/concepts/nope/graph").status_code == 404
