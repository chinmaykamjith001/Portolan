"""FastAPI application: health, budget, and the paper/chart routes (spec §12)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from portolan import __version__
from portolan.api.faq_api import router as faq_router
from portolan.api.findings_api import router as findings_router
from portolan.api.papers import router as papers_router
from portolan.api.repo_api import router as repo_router
from portolan.api.route_api import router as route_router
from portolan.api.services import Services
from portolan.api.settings_api import router as settings_router
from portolan.api.static import mount_frontend, static_dir_from_env
from portolan.config import Config, api_key_present, load_config
from portolan.ingest.http import PoliteClient
from portolan.llm.gateway import init_gateway
from portolan.llm.ratelimit import pacific_day
from portolan.orchestrator.events import EventBus
from portolan.orchestrator.queue import JobQueue
from portolan.store.chart import ChartStore
from portolan.store.db import open_database
from portolan.store.details import DetailStore


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def build_services(config: Config) -> Services:
    db = open_database(config.storage.db_path)
    bus = EventBus()
    svc = Services(
        config=config,
        db=db,
        gateway=init_gateway(config, db),
        http=PoliteClient(db, contact=config.http.contact),
        bus=bus,
        queue=JobQueue(db, bus=bus, workers=2, p0_workers=1),
        chart=ChartStore(db),
        details=DetailStore(db),
    )
    svc.gateway.on_event = svc.on_gateway_event
    return svc


async def _resume_on_new_day(svc: Services, every_seconds: float = 60.0) -> None:
    """The free-tier budget resets at midnight US Pacific: un-park paused jobs then."""
    day = pacific_day()
    while True:
        await asyncio.sleep(every_seconds)
        if (now := pacific_day()) != day:
            day = now
            svc.queue.resume_jobs()


def create_app(
    config: Config | None = None,
    services: Services | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        svc = services or build_services(config)
        svc.register_handlers()
        app.state.services = svc
        await svc.queue.start()
        svc.queue.resume_jobs()  # jobs paused by a used-up daily budget on an earlier run
        watcher = asyncio.create_task(_resume_on_new_day(svc))
        try:
            yield
        finally:
            watcher.cancel()
            await svc.queue.stop()
            if services is None:
                await svc.http.aclose()
                svc.db.close()

    app = FastAPI(title="Portolan", version=__version__, lifespan=lifespan)
    app.include_router(papers_router)
    app.include_router(route_router)
    app.include_router(repo_router)
    app.include_router(findings_router)
    app.include_router(faq_router)
    app.include_router(settings_router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def bad_request(_request: Request, _exc: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(422, "bad_request", "The request was missing something or malformed.")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, _exc: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(500, "internal", "Something went wrong on the server. Try again.")

    @app.get("/api/health")
    def health(request: Request) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        svc: Services = request.app.state.services
        provider = config.llm.provider
        return {
            "status": "ok",
            "version": __version__,
            "provider": provider,
            "provider_ready": api_key_present(provider),
            "unconfigured_roles": [] if provider == "mock" else config.llm.models.placeholders(),
            "free_tier_notice": (
                "Free Gemini keys may let Google use what you send. "
                "Don't analyze private code with a free key."
                if provider == "gemini"
                else None
            ),
            "gateway_provider": svc.gateway.provider.name,
        }

    @app.get("/api/budget")
    def budget(request: Request) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        svc: Services = request.app.state.services
        return svc.gateway.budget_state()

    # Opt in only (`portolan serve` sets PORTOLAN_STATIC_DIR), so tests never serve a stale build.
    static = static_dir or static_dir_from_env()
    if static is not None and (static / "index.html").is_file():
        mount_frontend(app, static)
    return app
