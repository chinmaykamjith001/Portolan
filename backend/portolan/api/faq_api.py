"""FAQ endpoints (spec §12). `GET /faq` returns `{items}` or 202 while the first build runs;
`POST /ask` waits for the (single, P0) answer."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from portolan.api.papers import error, services
from portolan.detail.explain import DetailError
from portolan.faq.build import answer_question
from portolan.store.faq import FaqStore

router = APIRouter(prefix="/api")


class AskIn(BaseModel):
    question: str


@router.get("/papers/{paper_id}/faq")
def faq(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    if svc.paper(paper_id) is None:
        return error(404, "not_found", "Portolan hasn't seen this paper yet. Paste it first.")
    items = FaqStore(svc.db).all(paper_id)
    key = f"faq:{paper_id}"
    job = svc.queue.get(key)
    if items or (job is not None and job.state == "done"):
        return JSONResponse({"items": [i.model_dump(mode="json") for i in items]})
    svc.queue.submit("faq", key, priority="P1", paper_id=paper_id)
    return JSONResponse({"status": "generating"}, status_code=202)


@router.post("/papers/{paper_id}/ask")
async def ask(paper_id: str, body: AskIn, request: Request) -> JSONResponse:
    svc = services(request)
    doc = svc.paper(paper_id)
    if doc is None:
        return error(404, "not_found", "Portolan hasn't seen this paper yet. Paste it first.")
    question = body.question.strip()
    if not question:
        return error(400, "empty_question", "Type a question first.")
    if len(question) > 500:
        return error(400, "question_too_long", "Keep the question under 500 characters.")
    try:
        item = await answer_question(doc, svc.gateway, svc.chart, svc.db, question)
    except DetailError as exc:
        return error(502, "answer_failed", str(exc))
    FaqStore(svc.db).save(paper_id, item)
    return JSONResponse(item.model_dump(mode="json"))
