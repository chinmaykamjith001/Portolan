"""Jumping into a layer keeps two levels below it ready (spec §10.5.2): checked through the API."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from portolan.api.app import Services
from tests.test_api_chart import ARXIV, FakeChartLLM, wait_for
from tests.test_api_chart import client as client


class DeepLLM(FakeChartLLM):
    """Every concept can be decomposed further (the stock fake turns into leaves after 60)."""

    def _concepts(self, prefix: str, n: int, leaf: bool) -> list[dict[str, Any]]:
        return super()._concepts(prefix, n, leaf=False)


@pytest.fixture
def fake() -> FakeChartLLM:
    return DeepLLM()


def test_jumping_in_prefetches_two_levels_below_the_new_layer(client: TestClient) -> None:
    svc: Services = client.app.state.services  # type: ignore[attr-defined]
    paper_id = client.post("/api/papers", json={"input": ARXIV}).json()["paper_id"]
    master = wait_for(
        lambda: (
            (r := client.get(f"/api/papers/{paper_id}/chart/master")).status_code == 200
            and r.json()
        )
    )
    wait_for(lambda: (j := svc.queue.get(f"frontier:{paper_id}:master")) is not None
             and j.state == "done", timeout=30)  # fmt: skip

    def members(cid: str) -> list[str]:
        layer = svc.chart.get_layer(paper_id, cid)
        assert layer is not None, f"no layer for {cid}"
        return layer.concepts

    def built(cid: str) -> bool:
        return svc.chart.get_layer(paper_id, cid) is not None

    child = master["layer"]["concepts"][0]
    children = members(child)  # level 2: built by the master's frontier
    assert all(built(c) for c in children)
    grandchildren = [g for c in children for g in members(c)]  # level 3
    assert grandchildren and not any(built(g) for g in grandchildren)  # not yet

    assert client.get(f"/api/papers/{paper_id}/chart/layers/{child}").status_code == 200  # jump in
    wait_for(lambda: (j := svc.queue.get(f"frontier:{paper_id}:{child}")) is not None
             and j.state == "done", timeout=30)  # fmt: skip
    # Two levels below the layer on screen now exist: the next jump never waits for a skeleton.
    assert all(built(g) for g in grandchildren)
