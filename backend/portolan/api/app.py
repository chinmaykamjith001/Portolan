"""FastAPI application. Routers for papers, chart, route, etc. are added milestone by milestone."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from portolan import __version__
from portolan.config import Config, api_key_present, load_config
from portolan.llm.gateway import Gateway, init_gateway
from portolan.store.db import open_database


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(config: Config | None = None, gateway: Gateway | None = None) -> FastAPI:
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if gateway is None:
            db = open_database(config.storage.db_path)
            app.state.gateway = init_gateway(config, db)
            try:
                yield
            finally:
                db.close()
        else:
            app.state.gateway = gateway
            yield

    app = FastAPI(title="Portolan", version=__version__, lifespan=lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, _exc: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(500, "internal", "Something went wrong on the server. Try again.")

    @app.get("/api/health")
    def health(request: Request) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        gw: Gateway = request.app.state.gateway
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
            "gateway_provider": gw.provider.name,
        }

    @app.get("/api/budget")
    def budget(request: Request) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        gw: Gateway = request.app.state.gateway
        return gw.budget_state()

    return app
