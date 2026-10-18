"""Paper, chart and event endpoints (spec §12). Handlers enqueue work and return immediately."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile

from portolan.api.jobs import failures, not_ingested, submit_or_report
from portolan.api.services import Services, describe_concepts, findings_index
from portolan.chart.issues import issues_inside
from portolan.ingest.arxiv import fetch_metadata
from portolan.ingest.http import FetchError
from portolan.ingest.pipeline import arxiv_paper_id, doi_paper_id, upload_paper_id
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
async def create_paper(request: Request) -> JSONResponse:
    """`{input}` as JSON, or a PDF as multipart form data (field `file`)."""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        return await _create_from_upload(request)
    try:
        body = PaperIn.model_validate(await request.json())
    except (ValueError, ValidationError):
        return error(422, "bad_request", "The request was missing something or malformed.")
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
        try:
            meta = await fetch_metadata(svc.http, ref.arxiv_id, None)
        except Exception:  # arXiv down or odd reply: ingestion finds the version itself
            meta = None
        if meta is not None:
            ref = ref.model_copy(update={"version": meta.version, "title": meta.title})
    if ref.arxiv_id:
        paper_id = arxiv_paper_id(f"{ref.arxiv_id}{ref.version or ''}")
    elif ref.doi:
        paper_id = doi_paper_id(ref.doi)
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


async def _create_from_upload(request: Request) -> JSONResponse:
    svc = services(request)
    cap = svc.config.storage.max_upload_mb * 1024 * 1024
    try:
        form = await request.form(max_files=1, max_fields=4)
    except Exception:  # malformed multipart body
        return error(400, "bad_upload", "Couldn't read the upload. Try choosing the PDF again.")
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return error(400, "bad_upload", "Choose a PDF file to upload.")
        data = await upload.read(cap + 1)
        filename = Path(upload.filename or "upload.pdf").name[:200] or "upload.pdf"
    finally:
        await form.close()
    if len(data) > cap:
        return error(
            413, "too_large",
            f"That PDF is larger than {svc.config.storage.max_upload_mb} MB. Try a smaller file.",
        )  # fmt: skip
    if not data.startswith(b"%PDF"):
        return error(400, "not_pdf", f"{filename} doesn't look like a PDF. Upload a PDF file.")

    paper_id = upload_paper_id(data)
    if svc.paper(paper_id) is not None:
        svc.queue.submit(
            "chart_master", f"chart_master:{paper_id}", priority="P0", paper_id=paper_id
        )
        return JSONResponse({"paper_id": paper_id, "status": "ready"})
    folder = svc.config.storage.uploads_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{paper_id.split(':', 1)[1]}.pdf"
    await asyncio.to_thread(path.write_bytes, data)
    svc.queue.submit(
        "ingest", f"ingest:{paper_id}", {"upload_path": str(path), "filename": filename},
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
            return JSONResponse(
                {
                    "paper_id": paper_id,
                    "status": "processing",
                    "jobs": jobs,
                    "failures": failures(svc, paper_id),
                },
                202,
            )
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
            "failures": failures(svc, paper_id),
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
    if (waiting := not_ingested(svc, paper_id)) is not None:
        return waiting
    return submit_or_report(
        svc, "chart_master", f"chart_master:{paper_id}", priority="P0", paper_id=paper_id
    )


@router.get("/papers/{paper_id}/chart/layers/{concept_id}")
def chart_layer(paper_id: str, concept_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    concept = svc.chart.get_concept(paper_id, concept_id)
    if concept is None:
        return error(404, "not_found", "That concept isn't on this paper's map.")
    payload = _layer_payload(svc, paper_id, concept_id)
    if payload is None and (
        concept.is_first_principle or concept.depth >= svc.config.generation.max_depth
    ):
        return error(404, "no_layer", "This is a first principle: there is nothing further inside.")
    if payload is not None:
        # Looking at this layer: keep two levels below it ready (spec §10.5.2), ahead of other P1.
        svc.queue.submit(
            "frontier", f"frontier:{paper_id}:{concept_id}", {"layer_id": concept_id},
            priority="P1", paper_id=paper_id, front=True,
        )  # fmt: skip
        return JSONResponse(payload)
    return submit_or_report(
        svc, "skeleton", f"skeleton:{paper_id}:{concept_id}", {"concept_id": concept_id},
        priority="P0", paper_id=paper_id,
    )  # fmt: skip


@router.get("/papers/{paper_id}/concepts/{concept_id}")
def concept(paper_id: str, concept_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    c = svc.chart.get_concept(paper_id, concept_id)
    if c is None:
        return error(404, "not_found", "That concept isn't on this paper's map.")
    attached = findings_index(svc, paper_id)
    inside = issues_inside(
        lambda cid: svc.chart.children(paper_id, cid), lambda cid: attached.get(cid, ()), concept_id
    )
    return JSONResponse(
        {
            "concept": c.model_dump(mode="json"),
            "also_appears_in": svc.chart.layers_containing(paper_id, concept_id),
            "issues_inside": inside,
            "details": {
                "explanation": svc.details.explanation(paper_id, concept_id, "default") is not None,
                "code": svc.details.grounding(paper_id, concept_id) is not None,
                "example": svc.details.example(paper_id, concept_id, 0) is not None,
                "graph": svc.details.graph(paper_id, concept_id)[1] is not None,
            },
        }
    )


ANGLES = {"default", "simpler", "mathematical", "analogy", "why", "another_take"}


@router.get("/papers/{paper_id}/concepts/{concept_id}/explanation")
def explanation(
    paper_id: str, concept_id: str, request: Request, angle: str = "default"
) -> JSONResponse:
    svc = services(request)
    if angle not in ANGLES:
        return error(400, "bad_angle", f"Unknown angle {angle!r}.")
    if svc.chart.get_concept(paper_id, concept_id) is None:
        return error(404, "not_found", "That concept isn't on this paper's map.")
    stored = svc.details.explanation(paper_id, concept_id, angle)
    if stored is not None:
        return JSONResponse(stored.model_dump(mode="json"))
    key = f"explain:{paper_id}:{concept_id}:{angle}"
    return submit_or_report(
        svc, "explain", key, {"concept_id": concept_id, "angle": angle},
        priority="P0", paper_id=paper_id,
    )  # fmt: skip


@router.get("/papers/{paper_id}/links/{link_id}/explanation")
def edge_explanation(paper_id: str, link_id: str, request: Request) -> JSONResponse:
    from portolan.detail.explain import find_link

    svc = services(request)
    if find_link(svc.chart, paper_id, link_id) is None:
        return error(404, "not_found", "That link isn't on this paper's map.")
    stored = svc.details.edge(paper_id, link_id)
    if stored is not None:
        return JSONResponse(stored.model_dump(mode="json"))
    return submit_or_report(
        svc, "edge", f"edge:{paper_id}:{link_id}", {"link_id": link_id},
        priority="P0", paper_id=paper_id,
    )  # fmt: skip


def _concept_or_404(svc: Services, paper_id: str, concept_id: str) -> JSONResponse | None:
    if svc.chart.get_concept(paper_id, concept_id) is None:
        return error(404, "not_found", "That concept isn't on this paper's map.")
    return None


def _generating(
    svc: Services,
    paper_id: str,
    kind: str,
    key: str,
    payload: dict[str, Any],
    priority: Literal["P0", "P1"] = "P0",
) -> JSONResponse:
    return submit_or_report(svc, kind, key, payload, priority=priority, paper_id=paper_id)


@router.get("/papers/{paper_id}/concepts/{concept_id}/code")
def concept_code(paper_id: str, concept_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    if (missing := _concept_or_404(svc, paper_id, concept_id)) is not None:
        return missing
    stored = svc.details.grounding(paper_id, concept_id)
    if stored is not None:
        return JSONResponse(stored.model_dump(mode="json"))
    return _generating(
        svc, paper_id, "ground", f"ground:{paper_id}:{concept_id}", {"concept_id": concept_id}
    )


@router.get("/papers/{paper_id}/concepts/{concept_id}/example")
def concept_example(
    paper_id: str, concept_id: str, request: Request, variant: int = 0
) -> JSONResponse:
    svc = services(request)
    if (missing := _concept_or_404(svc, paper_id, concept_id)) is not None:
        return missing
    if variant < 0 or variant > 8:
        return error(400, "bad_variant", "Ask for example 0 to 8.")
    stored = svc.details.example(paper_id, concept_id, variant)
    if stored is not None:
        return JSONResponse(stored.model_dump(mode="json"))
    return _generating(
        svc,
        paper_id,
        "example",
        f"example:{paper_id}:{concept_id}:{variant}",
        {"concept_id": concept_id, "variant": variant},
    )


@router.get("/papers/{paper_id}/concepts/{concept_id}/graph")
def concept_graph(paper_id: str, concept_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    if (missing := _concept_or_404(svc, paper_id, concept_id)) is not None:
        return missing
    generated, spec = svc.details.graph(paper_id, concept_id)
    if generated:
        return JSONResponse(spec.model_dump(mode="json") if spec else {"template": "none"})
    # Nobody is waiting on a graph (the tab appears only when one exists), so it yields to P0 work.
    return _generating(
        svc,
        paper_id,
        "graph",
        f"graph:{paper_id}:{concept_id}",
        {"concept_id": concept_id},
        priority="P1",
    )
