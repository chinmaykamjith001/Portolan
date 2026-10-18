"""Long-lived backend services and the job handlers that turn API requests into work.

One process holds one of each: the DB, the LLM gateway, the polite HTTP client (so per-host
politeness spacing is shared), the event bus and the job queue. Handlers publish SSE events as
results land so the browser never polls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from portolan.chart.issues import issues_inside
from portolan.chart.master import ChartBuilder
from portolan.code.ensure import ensure_index
from portolan.config import Config
from portolan.detail.example import make_example
from portolan.detail.explain import explain, explain_edge
from portolan.detail.graph import make_graph
from portolan.detail.ground import ground_code
from portolan.faq.build import build_faq
from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import ingest_ref, parse_upload
from portolan.llm.gateway import Gateway
from portolan.models.concept import MASTER
from portolan.models.finding import FAQItem, Finding
from portolan.models.paper import PaperDoc, PaperRef
from portolan.orchestrator.events import EventBus
from portolan.orchestrator.queue import Job, JobQueue
from portolan.repo.finder import RepoFinder
from portolan.route.build import build_main_route, build_sub_route
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.details import DetailStore
from portolan.store.findings import FindingStore
from portolan.store.papers import load_paper, save_paper
from portolan.store.repos import choose_repo, chosen_repo, save_candidates
from portolan.store.routes import RouteStore
from portolan.verify.engine import verify_paper
from portolan.verify.issues import findings_by_concept

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
            "index": self._index,
            "ground": self._ground,
            "example": self._example,
            "graph": self._graph,
            "route_main": self._route_main,
            "route_sub": self._route_sub,
            "verify": self._verify,
            "faq": self._faq,
        }
        for kind, handler in handlers.items():
            self.queue.register(kind, _with_paper(handler))

    def on_gateway_event(self, kind: str, data: dict[str, Any]) -> None:
        paper_id = current_paper.get()
        if kind == "rate_limited" and paper_id is not None:
            self.bus.publish(paper_id, "rate_limited", data)

    async def _ingest(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        self.progress(paper_id, "reading", 5, "Reading paper")
        if "upload_path" in job.payload:
            data = await asyncio.to_thread(Path(job.payload["upload_path"]).read_bytes)
            doc = await asyncio.to_thread(parse_upload, data, job.payload.get("filename", ""))
            save_paper(self.db, doc)
        else:
            ref = PaperRef.model_validate(job.payload["ref"])
            pdf_url = job.payload.get("pdf_url")
            doc = (await ingest_ref(ref, self.http, self.db, pdf_url=pdf_url)).doc
            if doc.paper_id != paper_id:
                # The version was unknown when the id was handed out ("arxiv:X", now "arxiv:Xv2"):
                # keep the id the browser holds, or the page would wait for it forever.
                doc = doc.model_copy(update={"paper_id": paper_id})
                save_paper(self.db, doc)
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
        if chosen_repo(self.db, paper_id) is not None:
            self.queue.submit("index", f"index:{paper_id}", priority="P1", paper_id=paper_id)
        else:  # no code to verify: go straight to the FAQ
            self.queue.submit("faq", f"faq:{paper_id}", priority="P2", paper_id=paper_id)

    async def _index(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        repo = chosen_repo(self.db, paper_id)
        if repo is None:
            return
        self.progress(paper_id, "finding_code", 45, "Reading the code")
        ok = await ensure_index(self.config, self.db, paper_id, repo)
        self.progress(
            paper_id, "finding_code", 50, "Code ready" if ok else "Couldn't download the code"
        )
        # Verification and the FAQ are background work (P2); the Chart never waits for them.
        # `force`: this job runs again only when the paper's code changed, so recheck everything.
        kind = "verify" if ok else "faq"
        self.queue.submit(kind, f"{kind}:{paper_id}", priority="P2", paper_id=paper_id, force=True)

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
        self.queue.submit("route_main", f"route_main:{paper_id}", priority="P1", paper_id=paper_id)
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
        if self.chart.get_layer(paper_id, concept_id) is None:
            # Finishing "done" with no layer would leave the page waiting forever: fail visibly.
            raise RuntimeError("the model's answer had no usable layer for this idea")
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

    async def _ground(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        concept_id = str(job.payload["concept_id"])
        repo = chosen_repo(self.db, paper_id)
        if repo is not None:
            await ensure_index(self.config, self.db, paper_id, repo)  # no-op once indexed
        grounding = await ground_code(doc, self.gateway, self.chart, self.db, concept_id)
        self.details.save_grounding(paper_id, grounding)
        self.bus.publish(paper_id, "detail_ready", {"concept_id": concept_id, "part": "code"})

    async def _example(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        concept_id, variant = str(job.payload["concept_id"]), int(job.payload.get("variant", 0))
        previous = [e for e in self.details.examples(paper_id, concept_id) if e.variant < variant]
        example = await make_example(doc, self.gateway, self.chart, concept_id, variant, previous)
        self.details.save_example(paper_id, example)
        self.bus.publish(
            paper_id,
            "detail_ready",
            {"concept_id": concept_id, "part": "example", "variant": variant},
        )

    async def _graph(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        concept_id = str(job.payload["concept_id"])
        spec = await make_graph(doc, self.gateway, self.chart, concept_id)
        self.details.save_graph(paper_id, concept_id, spec)
        self.bus.publish(paper_id, "detail_ready", {"concept_id": concept_id, "part": "graph"})

    async def _route_main(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        routes = RouteStore(self.db)
        route = await build_main_route(doc, self.gateway, self.chart, routes)
        routes.save(route)
        self.bus.publish(paper_id, "route_ready", {"route_id": route.id})

    async def _route_sub(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return
        routes = RouteStore(self.db)
        stop_id = str(job.payload["stop_id"])
        route = await build_sub_route(doc, self.gateway, self.chart, routes, stop_id)
        routes.save(route)
        self.bus.publish(paper_id, "route_ready", {"route_id": route.id})

    async def _verify(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return

        def on_finding(f: Finding) -> None:
            self.bus.publish(
                paper_id, "finding_added", {"finding_id": f.id, "concept_ids": f.concept_ids}
            )

        await verify_paper(doc, self.gateway, self.chart, self.db, on_finding)
        self.bus.publish(paper_id, "finding_added", {"finding_id": None, "concept_ids": []})
        self.queue.submit("faq", f"faq:{paper_id}", priority="P2", paper_id=paper_id, force=True)

    async def _faq(self, job: Job) -> None:
        paper_id = str(job.paper_id)
        doc = self.paper(paper_id)
        if doc is None:
            return

        def on_item(_item: FAQItem) -> None:
            self.bus.publish(paper_id, "faq_ready", {})

        await build_faq(doc, self.gateway, self.chart, self.db, self.http, on_item)
        self.bus.publish(paper_id, "faq_ready", {})

    def job_states(self, paper_id: str) -> dict[str, str]:
        states: dict[str, str] = {}
        for kind in ("ingest", "repos", "chart_master"):
            job = self.queue.get(f"{kind}:{paper_id}")
            if job is not None:
                states[kind] = job.state
        return states


def findings_index(services: Services, paper_id: str) -> dict[str, set[str]]:
    """concept id -> ids of the confident mismatches attached to it (the map's issue badges)."""
    threshold = services.config.generation.mismatch_confidence_threshold
    return findings_by_concept(FindingStore(services.db).all(paper_id), threshold)


def describe_concepts(services: Services, paper_id: str, ids: list[str]) -> dict[str, Any]:
    concepts = services.chart.concepts(paper_id, ids)
    attached = findings_index(services, paper_id)
    out: dict[str, Any] = {}
    for cid, c in concepts.items():
        out[cid] = c.model_dump(mode="json") | {
            "issues_inside": issues_inside(
                lambda x: services.chart.children(paper_id, x),
                lambda x: attached.get(x, ()),
                cid,
            )
        }
    return out
