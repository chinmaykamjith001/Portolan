"""Findings endpoint (spec §12). Returns `{findings, state}`; `state` is the verification run's
state (`none` before it starts, then `running`, `done` or `failed`)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from portolan.api.papers import error, services
from portolan.store.findings import FindingStore

router = APIRouter(prefix="/api")


@router.get("/papers/{paper_id}/findings")
def findings(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    if svc.paper(paper_id) is None:
        return error(404, "not_found", "Portolan hasn't seen this paper yet. Paste it first.")
    store = FindingStore(svc.db)
    return JSONResponse(
        {
            "findings": [f.model_dump(mode="json") for f in store.all(paper_id)],
            "state": store.run_state(paper_id) or "none",
        }
    )
