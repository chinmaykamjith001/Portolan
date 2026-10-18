"""Serve the built frontend from the API process, so one `portolan serve` is the whole app."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from portolan.config import repo_root

# Windows takes MIME types from the registry, where .js is sometimes text/plain: browsers then
# refuse to run the app's module scripts.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

STATIC_ENV = "PORTOLAN_STATIC_DIR"


def default_static_dir() -> Path | None:
    """`frontend/dist` of this checkout, if it has been built."""
    dist = repo_root() / "frontend" / "dist"
    return dist if (dist / "index.html").is_file() else None


def static_dir_from_env() -> Path | None:
    raw = os.environ.get(STATIC_ENV)
    return Path(raw) if raw else None


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """`/assets/*` as files, any other GET → the file if it exists, else index.html (SPA).

    Paths under /api never fall through to the SPA: they get the JSON error envelope.
    """
    root = static_dir.resolve()
    index = root / "index.html"
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="There is no such API endpoint.")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)
