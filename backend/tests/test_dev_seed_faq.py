"""The demo FAQ is valid against the demo paper and map, and is served without any LLM."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.api.app import build_services, create_app
from portolan.config import Config, ModelsConfig
from portolan.dev.seed import DEMO_PAPER_ID, demo_paper, seed_demo
from portolan.dev.seed_faq import ITEMS, UNSUPPORTED
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.faq import FaqStore


class NoLLM:
    name = "no-llm"
    uses_quota = False

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        raise AssertionError(f"unexpected LLM call: {ctx.template_id}")


def test_demo_faq_is_consistent_with_the_demo_paper(db: Database) -> None:
    seed_demo(db)
    seed_demo(db)  # idempotent: same ids replace
    items = FaqStore(db).all(DEMO_PAPER_ID)
    assert len(items) == len(ITEMS) >= 6
    assert {i.source_kind for i in items} >= {
        "reviewer_discussion", "github_issue", "finding", "anticipated", "user_asked",
    }  # fmt: skip
    anchors = {a.id for a in demo_paper().anchors}
    concepts = set(ChartStore(db).concepts(DEMO_PAPER_ID))
    for i in items:
        assert set(i.anchors_cited) <= anchors, i.id
        assert set(i.concept_ids) <= concepts, i.id
        if i.supported:
            assert i.anchors_cited or i.source_url, f"{i.id} cites nothing"
    (unsupported,) = [i for i in items if not i.supported]
    assert unsupported.answer_markdown == UNSUPPORTED and unsupported.anchors_cited == []
    assert any(i.source_label.startswith("From GitHub issue #") for i in items)


def test_demo_faq_is_served_with_no_llm(tmp_path: Path) -> None:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    seed_demo(svc.db)
    svc.gateway = Gateway(config, svc.db, provider=NoLLM(), on_event=svc.on_gateway_event)
    with TestClient(create_app(config, services=svc)) as client:
        res = client.get(f"/api/papers/{DEMO_PAPER_ID}/faq")
        assert res.status_code == 200
        body = res.json()["items"]
        assert len(body) == len(ITEMS)
        assert {"reviewer_discussion", "github_issue"} <= {i["source_kind"] for i in body}
