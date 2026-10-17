"""Long-lived backend services and the job handlers that turn API requests into work.

One process holds one of each: the DB, the LLM gateway, the polite HTTP client (so per-host
politeness spacing is shared), the event bus and the job queue. Handlers publish SSE events as
results land so the browser never polls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from portolan.chart.master import ChartBuilder
from portolan.config import Config
from portolan.detail.explain import explain, explain_edge
from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import ingest_ref
from portolan.llm.gateway import Gateway
from portolan.models.concept import MASTER
from portolan.models.paper import PaperDoc, PaperRef
from portolan.orchestrator.events import EventBus
from portolan.orchestrator.queue import Job, JobQueue
from portolan.repo.finder import RepoFinder
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.details import DetailStore
from portolan.store.papers import load_paper
from portolan.store.repos import choose_repo, chosen_repo, save_candidates

# The paper whose job is running in this task, so gateway events (rate limits) reach its SSE stream.
current_paper: ContextVar[str | None] = ContextVar("current_paper", default=None)


def _with_paper(handler: Callable[[Job], Awaitable[None]]) -> Callable[[Job], Awaitable[None]]:
    async def run(job: Job) -> None:
        token = current_paper.set(job.paper_id)
        try:
            await handler(job)
        finally:
            current_paper.reset(token)

    return run


@dataclass
class Services:
    config: Config
    db: Database
    gateway: Gateway
    http: PoliteClient
    bus: EventBus
    queue: JobQueue
    chart: ChartStore
    details: DetailStore

    def progress(self, paper_id: str, stage: str, percent: int, message: str) -> None:
        self.bus.publish(
            paper_id, "progress", {"stage": stage, "percent": percent, "message": message}
        )

    def paper(self, paper_id: str) -> PaperDoc | None:
        return load_paper(self.db, paper_id)

    def builder(self, doc: PaperDoc) -> ChartBuilder:
        return ChartBuilder(
            doc,
            self.gateway,
            self.chart,
            batch_size=self.config.generation.skeleton_batch_size,
            max_depth=self.config.generation.max_depth,
        )

    # -- jobs -----------------------------------------------------------------------------

    def register_handlers(self) -> None:
        handlers = {
            "ingest": self._ingest,
            "repos": self._repos,
            "chart_master": self._chart_master,
            "frontier": self._frontier,
            "skeleton": self._skeleton,
            "explain": self._explain,
            "edge": self._edge,
        }
        for kind, handler in handlers.items():
            self.queue.register(kind, _with_paper(handler))

    def on_gateway_event(self, kind: str, data: dict[str, Any]) -> None:
        paper_id = current_paper.get()
        if kind == "rate_limited" and paper_id is not None:
            self.bus.publish(paper_id, "rate_limited", data)

    async def _ingest(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        ref = PaperRef.model_validate(job.payload["ref"])
        self.progress(paper_id, "reading", 5, "Reading paper")
        report = await ingest_ref(ref, self.http, self.db, pdf_url=job.payload.get("pdf_url"))
        doc = report.doc
        self.progress(paper_id, "reading", 25, f"Read {len(doc.anchors)} parts of the paper")
        self.queue.submit(
            "chart_master", f"chart_master:{paper_id}", priority="P0", paper_id=paper_id
        )
        self.queue.submit("repos", f"repos:{paper_id}", priority="P1", paper_id=paper_id)

    async def _repos(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        self.progress(paper_id, "finding_code", 30, "Finding code")
        result = await RepoFinder(self.http).find(doc)
        save_candidates(self.db, paper_id, result.candidates, result.status)
        if chosen_repo(self.db, paper_id) is None and result.best is not None:
            choose_repo(self.db, paper_id, result.best)
        self.progress(paper_id, "finding_code", 40, result.status.message)

    async def _chart_master(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        self.progress(paper_id, "building_map", 50, "Building map")
        layer = await self.builder(doc).master(priority="P0")
        self.bus.publish(
            paper_id, "layer_ready", {"layer_id": MASTER, "concepts": len(layer.concepts)}
        )
        self.progress(paper_id, "building_map", 70, "Map ready; preparing the next levels")
        self.queue.submit(
            "frontier", f"frontier:{paper_id}:{MASTER}", {"layer_id": MASTER},
            priority="P1", paper_id=paper_id,
        )  # fmt: skip

    async def _frontier(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        made = await self.builder(doc).ensure_frontier(str(job.payload["layer_id"]), priority="P1")
        for layer in made:
            self.bus.publish(
                paper_id, "layer_ready", {"layer_id": layer.id, "concepts": len(layer.concepts)}
            )
        if job.payload["layer_id"] == MASTER:
            self.progress(paper_id, "building_map", 100, "Map ready")

    async def _skeleton(self, job: Job) -> None:
        """P0: the user jumped into a concept whose layer doesn't exist yet."""
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        concept_id = str(job.payload["concept_id"])
        for layer in await self.builder(doc).skeletons([concept_id], priority="P0"):
            self.bus.publish(
                paper_id, "layer_ready", {"layer_id": layer.id, "concepts": len(layer.concepts)}
            )
        self.queue.submit(
            "frontier", f"frontier:{paper_id}:{concept_id}", {"layer_id": concept_id},
            priority="P1", paper_id=paper_id, front=True,
        )  # fmt: skip

    async def _explain(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        concept_id, angle = str(job.payload["concept_id"]), job.payload.get("angle", "default")
        explanation, _warnings = await explain(doc, self.gateway, self.chart, concept_id, angle)
        self.details.save_explanation(paper_id, explanation)
        self.bus.publish(
            paper_id,
            "detail_ready",
            {"concept_id": concept_id, "part": "explanation", "angle": angle},
        )

    async def _edge(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        link_id = str(job.payload["link_id"])
        self.details.save_edge(paper_id, await explain_edge(doc, self.gateway, self.chart, link_id))
        self.bus.publish(paper_id, "detail_ready", {"link_id": link_id, "part": "edge"})

    def job_states(self, paper_id: str) -> dict[str, str]:
        states: dict[str, str] = {}
        for kind in ("ingest", "repos", "chart_master"):
            job = self.queue.get(f"{kind}:{paper_id}")
            if job is not None:
                states[kind] = job.state
        return states


def describe_concepts(services: Services, paper_id: str, ids: list[str]) -> dict[str, Any]:
    concepts = services.chart.concepts(paper_id, ids)
    return {cid: c.model_dump(mode="json") for cid, c in concepts.items()}
