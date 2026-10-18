"""The demo route (used by the Playwright tests): valid, S-shaped, served with no LLM."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.api.app import build_services, create_app
from portolan.config import Config, ModelsConfig
from portolan.dev.seed import DEMO_PAPER_ID, seed_demo
from portolan.dev.seed_route import APPENDIX_ID, main_route, sub_routes
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse
from portolan.store.chart import ChartStore
from portolan.store.papers import load_paper
from portolan.store.routes import RouteStore


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


def _seeded(tmp_path: Path) -> tuple[Config, TestClient]:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    seed_demo(svc.db)
    svc.gateway = Gateway(config, svc.db, provider=NoLLM(), on_event=svc.on_gateway_event)
    return config, TestClient(create_app(config, services=svc))


def test_seeded_route_is_consistent(tmp_path: Path) -> None:
    _config, client = _seeded(tmp_path)
    with client:
        svc = client.app.state.services  # type: ignore[attr-defined]
        doc = load_paper(svc.db, DEMO_PAPER_ID)
        assert doc is not None and doc.anchor(APPENDIX_ID) is not None
        order = {a.id: a.order for a in doc.anchors}
        main = main_route(DEMO_PAPER_ID)
        assert len(main.stops) == 9 and [s.order for s in main.stops] == list(range(1, 10))
        assert [s.id for s in main.stops] == [f"stop:{n}" for n in range(1, 10)]
        assert main.stops[-1].transition_to_next is None
        assert all(s.transition_to_next for s in main.stops[:-1])
        assert [s.segment_title for s in main.stops if s.segment_title] == [
            "§1 Introduction", "§2 Model", "§3 Training",
        ]  # fmt: skip
        assert [s.id for s in main.stops if s.side_roads] == ["stop:8"]
        recurring = {s.concept_id for s in main.stops if s.first_stop_id}
        assert recurring == {"self-attention"}
        first = next(s for s in main.stops if s.seen_again_at)
        assert first.id == "stop:3" and first.seen_again_at == ["stop:6"]
        assert [s.sub_route_id for s in main.stops if s.sub_route_id] == ["stop:2", "stop:3"]
        for sub in sub_routes(DEMO_PAPER_ID):
            parent = next(s for s in main.stops if s.id == sub.id)
            assert parent.sub_route_id == sub.id and sub.parent_stop_id == sub.id
            assert [s.id for s in sub.stops] == [f"{sub.id}.{k}" for k in (1, 2)]
            positions = [order[s.anchor_id] for s in sub.stops]
            assert positions == sorted(positions)
        assert RouteStore(svc.db).get(DEMO_PAPER_ID, "main") == main
        assert ChartStore(svc.db).get_concept(DEMO_PAPER_ID, "toyformer") is not None


def test_seeded_route_is_served_without_llm(tmp_path: Path) -> None:
    config, client = _seeded(tmp_path)
    base = f"/api/papers/{DEMO_PAPER_ID}/route"
    with client:
        main = client.get(f"{base}/main")
        assert main.status_code == 200 and len(main.json()["stops"]) == 9
        sub = client.get(f"{base}/stops/stop:2/sub")
        assert sub.status_code == 200 and sub.json()["id"] == "stop:2"
        assert client.get(f"{base}/stops/stop:3/sub").status_code == 200
        leaf = client.get(f"{base}/stops/stop:5/sub")
        assert leaf.status_code == 404 and leaf.json()["error"]["code"] == "no_sub_route"
        assert main.json()["stops"][7]["side_roads"] == [APPENDIX_ID]
    assert NoLLM.calls == 0
    assert config.llm.provider == "mock"


def test_seeding_twice_is_idempotent(tmp_path: Path) -> None:
    _config, client = _seeded(tmp_path)
    with client:
        svc = client.app.state.services  # type: ignore[attr-defined]
        seed_demo(svc.db)
        doc = load_paper(svc.db, DEMO_PAPER_ID)
        assert doc is not None
        assert [a.id for a in doc.anchors].count(APPENDIX_ID) == 1
