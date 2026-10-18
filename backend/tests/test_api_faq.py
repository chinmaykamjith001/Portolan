"""GET /faq (202 while building, then items) and POST /ask, through the real app and job queue."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse
from tests.faq_helpers import FakeFaqLLM
from tests.test_api_chart import ARXIV, FakeChartLLM, wait_for
from tests.test_api_chart import client as chart_client  # noqa: F401  (fixture)

SCORES = "How is the row-wise softmax over scaled dot products stabilized by subtracting the max?"


class FaqChartLLM(FakeChartLLM):
    """Chart replies from the parent; the FAQ templates go to the shared fake."""

    def __init__(self) -> None:
        super().__init__()
        self.faq = FakeFaqLLM()

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        if ctx.template_id in {"faq_answer", "faq_anticipated", "ask_answer"}:
            return await self.faq.complete(
                model=model, messages=messages, temperature=temperature, schema=schema, ctx=ctx
            )
        return await super().complete(
            model=model, messages=messages, temperature=temperature, schema=schema, ctx=ctx
        )


@pytest.fixture
def fake() -> FaqChartLLM:
    return FaqChartLLM()


@pytest.fixture
def client(chart_client: TestClient) -> Iterator[TestClient]:  # noqa: F811
    yield chart_client


def open_paper(client: TestClient) -> str:
    created = client.post("/api/papers", json={"input": f"https://arxiv.org/abs/{ARXIV}"})
    paper_id: str = created.json()["paper_id"]
    wait_for(lambda: client.get(f"/api/papers/{paper_id}/chart/master").status_code == 200)
    return paper_id


def test_faq_builds_in_the_background_then_serves_items(
    client: TestClient, fake: FaqChartLLM
) -> None:
    paper_id = open_paper(client)
    url = f"/api/papers/{paper_id}/faq"
    first = client.get(url)
    assert first.status_code in (200, 202)  # the background job may already have finished
    body = wait_for(
        lambda: (r := client.get(url)).status_code == 200 and r.json()["items"] and r.json()
    )
    kinds = {i["source_kind"] for i in body["items"]}
    assert kinds == {"anticipated"}  # no repo, no reviews, no findings in this fixture
    item = body["items"][0]
    assert item["source_label"] == "Anticipated" and item["anchors_cited"] and item["supported"]
    calls = fake.faq.count("faq_anticipated")
    client.get(url)
    assert fake.faq.count("faq_anticipated") == calls  # stored: no second request
    svc = client.app.state.services  # type: ignore[attr-defined]
    assert any(e.type == "faq_ready" for e in svc.bus.recent(paper_id))


def test_faq_for_an_unknown_paper(client: TestClient) -> None:
    assert client.get("/api/papers/arxiv:0000.00000v1/faq").status_code == 404
    ask = client.post("/api/papers/arxiv:0000.00000v1/ask", json={"question": "Why?"})
    assert ask.status_code == 404


def test_ask_answers_with_citations_and_keeps_the_answer(client: TestClient) -> None:
    paper_id = open_paper(client)
    res = client.post(f"/api/papers/{paper_id}/ask", json={"question": SCORES})
    assert res.status_code == 200
    item = res.json()
    assert item["supported"] and item["source_kind"] == "user_asked"
    assert item["source_label"] == "Your question" and item["anchors_cited"]
    listed = client.get(f"/api/papers/{paper_id}/faq")
    assert listed.status_code == 200  # the asked item itself makes the FAQ non-empty
    assert item["id"] in {i["id"] for i in listed.json()["items"]}


def test_ask_validates_the_question(client: TestClient) -> None:
    paper_id = open_paper(client)
    url = f"/api/papers/{paper_id}/ask"
    assert client.post(url, json={"question": "   "}).status_code == 400
    assert client.post(url, json={"question": "x" * 501}).status_code == 400
    assert client.post(url, json={}).status_code == 422


def test_ask_without_support_says_so(client: TestClient, fake: FaqChartLLM) -> None:
    paper_id = open_paper(client)
    unrelated = client.post(
        f"/api/papers/{paper_id}/ask", json={"question": "What is the airspeed of a swallow?"}
    )
    assert unrelated.status_code == 200
    assert unrelated.json()["supported"] is False
    assert unrelated.json()["answer_markdown"] == "The paper and code don't answer this."
    assert fake.faq.count("ask_answer") == 0  # nothing relevant retrieved: no request

    fake.faq.unsupported = True
    res = client.post(f"/api/papers/{paper_id}/ask", json={"question": SCORES}).json()
    assert res["supported"] is False and res["anchors_cited"] == []


def test_ask_failure_is_a_plain_error(client: TestClient, fake: FaqChartLLM) -> None:
    paper_id = open_paper(client)
    fake.faq.fail = True
    res = client.post(f"/api/papers/{paper_id}/ask", json={"question": SCORES})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "answer_failed"
    assert "Try again" in res.json()["error"]["message"]
