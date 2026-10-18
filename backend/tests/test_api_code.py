"""GET /concepts/{id}/code: 202 while the job runs, then the stored grounding."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.code.index_store import IndexStore
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from portolan.models.repo import RepoCandidate
from portolan.store.repos import choose_repo
from tests.test_api_chart import FakeChartLLM, wait_for
from tests.test_api_chart import client as chart_client  # noqa: F401  (fixture)
from tests.test_api_details import _master  # pyright: ignore[reportPrivateUsage]
from tests.test_code_store import REPO, SHA, Def, build_file, save_files


class FakeGroundLLM(FakeChartLLM):
    """Chart replies from the parent, plus a ground_code reply that mixes good and bad notes."""

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        if ctx.template_id == "ground_code":
            self.calls["ground_code"] = self.calls.get("ground_code", 0) + 1
            body = {
                "grounding": "in_code",
                "spans": [{"path": "model/idea.py", "start_line": 1, "end_line": 4}],
                "notes": [
                    {"line": 3, "tag": "§2.1", "note": "Returns 1.", "kind": "implements"},
                    {"line": 99, "tag": "§9", "note": "Not in a span.", "kind": "context"},
                ],
                "library_calls": [],
                "summary": "Lines 1-4 implement the idea.",
            }  # fmt: skip
            return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")
        return await super().complete(
            model=model, messages=messages, temperature=temperature, schema=schema, ctx=ctx
        )


@pytest.fixture
def fake() -> FakeGroundLLM:
    return FakeGroundLLM()


@pytest.fixture
def client(chart_client: TestClient) -> Iterator[TestClient]:  # noqa: F811
    yield chart_client


def test_code_flow_without_and_with_a_repo(client: TestClient, fake: FakeGroundLLM) -> None:
    paper_id, master = _master(client)
    first, second = master["layer"]["concepts"][:2]
    svc = client.app.state.services  # type: ignore[attr-defined]

    # No repo linked: still a normal answer, and no LLM request.
    url = f"/api/papers/{paper_id}/concepts/{first}/code"
    assert client.get(url).status_code == 202
    none = wait_for(lambda: (r := client.get(url)).status_code == 200 and r.json())
    assert (
        none["grounding"] == "concept_only"
        and none["summary"] == "No code is linked for this paper."
    )
    assert "ground_code" not in fake.calls

    # Link and index a repo whose symbol matches every "Idea N" concept.
    choose_repo(
        svc.db, paper_id,
        RepoCandidate(
            id=REPO, url="https://github.com/example/transformer", owner="example",
            name="transformer", source="paper_link", official=True, official_evidence=None,
            status="ok",
        ),
    )  # fmt: skip
    idea, text = build_file(
        "model/idea.py", [Def("idea", doc="The idea.", body=["return 1", "return 2"])]
    )
    save_files(IndexStore(svc.db), [(idea, text)])

    url = f"/api/papers/{paper_id}/concepts/{second}/code"
    assert client.get(url).status_code == 202
    done = wait_for(lambda: (r := client.get(url)).status_code == 200 and r.json())
    assert done["grounding"] == "in_code"
    assert [n["line"] for n in done["notes"]] == [3]  # the line-99 note was dropped
    assert done["spans"][0]["commit_sha"] == SHA
    assert done["code"]["model/idea.py"]["3"] == "    return 1"
    assert done["links"]["model/idea.py"].endswith(f"/blob/{SHA}/model/idea.py")
    assert fake.calls["ground_code"] == 1
    client.get(url)
    assert fake.calls["ground_code"] == 1  # stored: no second request

    concept = client.get(f"/api/papers/{paper_id}/concepts/{second}").json()
    assert concept["details"]["code"]
    assert concept["concept"]["grounding"] == "in_code"  # the node's shape follows the result
    events = [e for e in svc.bus.recent(paper_id) if e.type == "detail_ready"]
    assert events[-1].data == {"concept_id": second, "part": "code"}


def test_code_for_an_unknown_concept(client: TestClient) -> None:
    paper_id, _ = _master(client)
    assert client.get(f"/api/papers/{paper_id}/concepts/nope/code").status_code == 404
