"""Paper, chart and event endpoints (spec §12). Handlers enqueue work and return immediately."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from portolan.api.services import Services, describe_concepts
from portolan.chart.issues import issues_inside
from portolan.ingest.arxiv import fetch_metadata
from portolan.ingest.http import FetchError
from portolan.ingest.resolver import resolve
from portolan.models.concept import MASTER
from portolan.orchestrator.events import format_sse, heartbeat
from portolan.store.repos import chosen_repo, load_candidates

router = APIRouter(prefix="/api")
HEARTBEAT_SECONDS = 15.0


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def services(request: Request) -> Services:
    return request.app.state.services


class PaperIn(BaseModel):
    input: str


@router.post("/papers")
async def create_paper(body: PaperIn, request: Request) -> JSONResponse:
    svc = services(request)
    try:
        result = await resolve(body.input, svc.http)
    except FetchError as exc:
        return error(502, "fetch_failed", str(exc))
    if result.error:
        return error(400, "unrecognized_input", result.error)
    if result.ref is None:
        return JSONResponse(
            {
                "status": "needs_choice",
                "candidates": [
                    {
                        "arxiv_id": c.arxiv_id,
                        "version": c.version,
                        "title": c.title,
                        "authors": c.authors[:6],
                        "year": c.year,
                    }
                    for c in result.candidates
                ],
            }
        )
    ref = result.ref
    if ref.arxiv_id and ref.version is None:
        meta = await fetch_metadata(svc.http, ref.arxiv_id, None)
        if meta is not None:
            ref = ref.model_copy(update={"version": meta.version, "title": meta.title})
    if ref.arxiv_id:
        paper_id = f"arxiv:{ref.arxiv_id}{ref.version or ''}"
    elif ref.doi:
        paper_id = f"doi:{ref.doi.lower()}"
    else:
        return error(400, "unrecognized_input", "Paste an arXiv link, DOI, PDF or title.")

    if svc.paper(paper_id) is not None:
        svc.queue.submit(
            "chart_master", f"chart_master:{paper_id}", priority="P0", paper_id=paper_id
        )
        return JSONResponse({"paper_id": paper_id, "status": "ready"})
    svc.queue.submit(
        "ingest", f"ingest:{paper_id}",
        {"ref": ref.model_dump(mode="json"), "pdf_url": result.pdf_url},
        priority="P0", paper_id=paper_id,
    )  # fmt: skip
    return JSONResponse({"paper_id": paper_id, "status": "queued"}, status_code=202)


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    doc = svc.paper(paper_id)
    jobs = svc.job_states(paper_id)
    if doc is None:
        if jobs:
            return JSONResponse({"paper_id": paper_id, "status": "processing", "jobs": jobs}, 202)
        return error(404, "not_found", "Portolan hasn't seen this paper yet. Paste it first.")
    stored = load_candidates(svc.db, paper_id)
    repo = chosen_repo(svc.db, paper_id)
    return JSONResponse(
        {
            "paper_id": paper_id,
            "status": "ready",
            "title": doc.title,
            "authors": doc.authors,
            "arxiv_id": doc.ref.arxiv_id,
            "version": doc.ref.version,
            "source_kind": doc.ref.source_kind,
            "low_structure": doc.low_structure,
            "repo_status": stored[1].model_dump() if stored else None,
            "repo": repo.model_dump(mode="json") if repo else None,
            "chart_ready": svc.chart.get_layer(paper_id, MASTER) is not None,
            "jobs": jobs,
        }
    )


@router.get("/papers/{paper_id}/repo/candidates")
def repo_candidates(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    stored = load_candidates(svc.db, paper_id)
    if stored is None:
        return JSONResponse({"status": "pending"}, status_code=202)
    candidates, status = stored
    return JSONResponse(
        {
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "status": status.model_dump(),
        }
    )


@router.get("/papers/{paper_id}/events")
async def events(
    paper_id: str, request: Request, last_event_id: str | None = Header(default=None)
) -> StreamingResponse:
    svc = services(request)
    last = int(last_event_id) if last_event_id and last_event_id.isdigit() else None

    async def stream() -> AsyncIterator[str]:
        async with svc.bus.subscribe(paper_id, last) as sub:
            iterator = aiter(sub)
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(anext(iterator), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield heartbeat()
                    continue
                except StopAsyncIteration:
                    break
                yield format_sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _layer_payload(svc: Services, paper_id: str, layer_id: str) -> dict[str, Any] | None:
    layer = svc.chart.get_layer(paper_id, layer_id)
    if layer is None:
        return None
    return {
        "layer": layer.model_dump(mode="json"),
        "concepts": describe_concepts(svc, paper_id, [layer.center, *layer.concepts]),
    }


@router.get("/papers/{paper_id}/chart/master")
def chart_master(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    payload = _layer_payload(svc, paper_id, MASTER)
    if payload is not None:
        return JSONResponse(payload)
    if svc.paper(paper_id) is None and not svc.job_states(paper_id):
        return error(404, "not_found", "Portolan hasn't seen this paper yet. Paste it first.")
    svc.queue.submit("chart_master", f"chart_master:{paper_id}", priority="P0", paper_id=paper_id)
    return JSONResponse({"status": "generating"}, status_code=202)


@router.get("/papers/{paper_id}/chart/layers/{concept_id}")
def chart_layer(paper_id: str, concept_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    if svc.chart.get_concept(paper_id, concept_id) is None:
        return error(404, "not_found", "That concept isn't on this paper's map.")
    payload = _layer_payload(svc, paper_id, concept_id)
    if payload is not None:
        # Looking at this layer: keep two levels below it ready (spec §10.5.2), ahead of other P1.
        svc.queue.submit(
            "frontier", f"frontier:{paper_id}:{concept_id}", {"layer_id": concept_id},
            priority="P1", paper_id=paper_id, front=True,
        )  # fmt: skip
        return JSONResponse(payload)
    svc.queue.submit(
        "skeleton", f"skeleton:{paper_id}:{concept_id}", {"concept_id": concept_id},
        priority="P0", paper_id=paper_id,
    )  # fmt: skip
    return JSONResponse({"status": "generating"}, status_code=202)


@router.get("/papers/{paper_id}/concepts/{concept_id}")
def concept(paper_id: str, concept_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    c = svc.chart.get_concept(paper_id, concept_id)
    if c is None:
        return error(404, "not_found", "That concept isn't on this paper's map.")
    inside = issues_inside(
        lambda cid: svc.chart.children(paper_id, cid), lambda _cid: [], concept_id
    )  # findings arrive in M8
    return JSONResponse(
        {
            "concept": c.model_dump(mode="json"),
            "also_appears_in": svc.chart.layers_containing(paper_id, concept_id),
            "issues_inside": inside,
            "details": {"explanation": False, "code": False, "example": False, "graph": False},
        }
    )
