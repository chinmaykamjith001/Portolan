"""Route endpoints (spec §12)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from portolan.api.jobs import not_ingested, submit_or_report
from portolan.api.papers import error, services
from portolan.store.routes import RouteStore

router = APIRouter(prefix="/api")


@router.get("/papers/{paper_id}/route/main")
def route_main(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    stored = RouteStore(svc.db).get(paper_id, "main")
    if stored is not None:
        return JSONResponse(stored.model_dump(mode="json"))
    if (waiting := not_ingested(svc, paper_id)) is not None:
        return waiting
    return submit_or_report(
        svc, "route_main", f"route_main:{paper_id}", priority="P0", paper_id=paper_id
    )


@router.get("/papers/{paper_id}/route/stops/{stop_id}/sub")
def route_sub(paper_id: str, stop_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    store = RouteStore(svc.db)
    parent = store.find_stop_route(paper_id, stop_id)
    stop = next((s for s in parent.stops if s.id == stop_id), None) if parent else None
    if stop is None:
        return error(404, "not_found", "That stop isn't on this paper's route.")
    if stop.sub_route_id is None:
        return error(404, "no_sub_route", "There is nothing further to open under this stop.")
    stored = store.get(paper_id, stop.sub_route_id)
    if stored is not None:
        return JSONResponse(stored.model_dump(mode="json"))
    return submit_or_report(
        svc, "route_sub", f"route_sub:{paper_id}:{stop_id}", {"stop_id": stop_id},
        priority="P0", paper_id=paper_id,
    )  # fmt: skip
