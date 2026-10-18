"""A failed background job must show up as an error, not as "generating" forever."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import BaseModel

from portolan.api.app import Services
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import MockFixtureMissing, ProviderResponse
from tests.test_api_chart import ARXIV, FakeChartLLM, wait_for
from tests.test_api_chart import client as client
from tests.test_api_chart import fake as fake


class NoFixturesLLM:
    """What the mock provider does for a paper nobody recorded."""

    name = "mock"
    uses_quota = False

    async def complete(
        self,
        *,
        model: str,
        messages: list[object],
        temperature: float,
        schema: type[BaseModel] | None,
        ctx: CallContext,
    ) -> ProviderResponse:
        raise MockFixtureMissing(ctx.template_id, 2, "abc123")


def _swap_llm(svc: Services, provider: object) -> None:
    svc.gateway = Gateway(svc.config, svc.db, provider=provider, on_event=svc.on_gateway_event)  # type: ignore[arg-type]


def _expire_cooldown(svc: Services, key: str) -> None:
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    svc.db.execute("UPDATE jobs SET updated_at = ? WHERE job_key = ?", (old, key))


def test_failed_master_is_reported_and_not_retried_in_a_loop(
    client: TestClient, fake: FakeChartLLM
) -> None:
    svc: Services = client.app.state.services  # type: ignore[attr-defined]
    _swap_llm(svc, NoFixturesLLM())
    paper_id = client.post("/api/papers", json={"input": ARXIV}).json()["paper_id"]
    key = f"chart_master:{paper_id}"
    wait_for(lambda: (j := svc.queue.get(key)) is not None and j.state == "failed")

    first = client.get(f"/api/papers/{paper_id}/chart/master")
    assert first.status_code == 502
    err = first.json()["error"]
    assert err["code"] == "generation_failed"
    assert "mock mode" in err["message"] and "PORTOLAN_LLM_PROVIDER=gemini" in err["message"]

    job = svc.queue.get(key)
    assert job is not None
    attempts = job.attempts
    for _ in range(5):  # a page that keeps asking must not re-run the failing job
        assert client.get(f"/api/papers/{paper_id}/chart/master").status_code == 502
    after = svc.queue.get(key)
    assert after is not None and after.attempts == attempts and after.state == "failed"

    info = client.get(f"/api/papers/{paper_id}").json()
    assert info["status"] == "ready" and info["chart_ready"] is False
    assert "mock mode" in info["failures"]["chart_master"]

    # After the cooldown the next ask runs it again, and with a working provider it succeeds.
    _expire_cooldown(svc, key)
    _swap_llm(svc, fake)
    wait_for(
        lambda: (r := client.get(f"/api/papers/{paper_id}/chart/master")).status_code == 200 and r
    )
    assert client.get(f"/api/papers/{paper_id}").json()["failures"] == {}


def test_still_failing_job_reruns_once_after_the_cooldown(client: TestClient) -> None:
    svc: Services = client.app.state.services  # type: ignore[attr-defined]
    _swap_llm(svc, NoFixturesLLM())
    paper_id = client.post("/api/papers", json={"input": ARXIV}).json()["paper_id"]
    key = f"chart_master:{paper_id}"
    wait_for(lambda: (j := svc.queue.get(key)) is not None and j.state == "failed")
    before = svc.queue.get(key)
    assert before is not None

    _expire_cooldown(svc, key)
    assert client.get(f"/api/papers/{paper_id}/chart/master").status_code == 202
    wait_for(
        lambda: (
            (j := svc.queue.get(key)) is not None
            and j.state == "failed"
            and j.attempts == before.attempts + 1
        )
    )
    assert client.get(f"/api/papers/{paper_id}/chart/master").status_code == 502
