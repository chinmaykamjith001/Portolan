"""Choosing or uploading a paper's code (spec §12 `POST /api/papers/{id}/repo`, §10.3).

Body: `{"candidate_id": ...}` to pick a found candidate, `{"local_path": ...}` for a folder on this
computer (only from a browser on the same machine), or multipart with a `file` field holding a
zip. Changing the code clears what was derived from the old code (code notes, findings) and
re-indexes in the background.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from portolan.api.papers import error, services
from portolan.api.services import Services
from portolan.models.repo import RepoCandidate
from portolan.repo.upload import UploadError, extract_zip, use_folder
from portolan.store.findings import FindingStore
from portolan.store.repos import choose_repo, load_candidates

router = APIRouter(prefix="/api")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _use(svc: Services, paper_id: str, cand: RepoCandidate) -> JSONResponse:
    choose_repo(svc.db, paper_id, cand)
    svc.details.delete_groundings(paper_id)
    FindingStore(svc.db).delete_paper(paper_id)
    svc.chart.set_issue_counts(paper_id, {})
    svc.queue.submit("index", f"index:{paper_id}", priority="P1", paper_id=paper_id, force=True)
    return JSONResponse({"repo": cand.model_dump(mode="json"), "status": "indexing"})


@router.post("/papers/{paper_id}/repo")
async def set_repo(paper_id: str, request: Request) -> JSONResponse:
    svc = services(request)
    if svc.paper(paper_id) is None:
        return error(404, "not_found", "Portolan hasn't seen this paper yet. Paste it first.")
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        return await _from_zip(svc, paper_id, request)
    try:
        raw: object = await request.json()
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        return error(400, "bad_request", "Send a candidate_id, a local_path or a zip file.")
    body = cast(dict[str, object], raw)
    if isinstance(cid := body.get("candidate_id"), str):
        stored = load_candidates(svc.db, paper_id)
        cand = next((c for c in (stored[0] if stored else []) if c.id == cid), None)
        if cand is None:
            return error(404, "not_found", "That code candidate isn't in this paper's list.")
        return _use(svc, paper_id, cand)
    if isinstance(local := body.get("local_path"), str):
        host = request.client.host if request.client else ""
        if host not in LOCAL_HOSTS:
            return error(403, "not_local", "A folder path only works from this computer.")
        try:
            cand = await asyncio.to_thread(use_folder, local)
        except UploadError as exc:
            return error(400, "bad_folder", str(exc))
        return _use(svc, paper_id, cand)
    return error(400, "bad_request", "Send a candidate_id, a local_path or a zip file.")


async def _from_zip(svc: Services, paper_id: str, request: Request) -> JSONResponse:
    cap = svc.config.storage.max_upload_mb * 1024 * 1024
    try:
        form = await request.form(max_files=1, max_fields=4)
    except Exception:  # malformed multipart body
        return error(400, "bad_upload", "Couldn't read the upload. Try choosing the zip again.")
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return error(400, "bad_upload", "Choose a zip file of the code to upload.")
        data = await upload.read(cap + 1)
        filename = Path(upload.filename or "code.zip").name[:200] or "code.zip"
    finally:
        await form.close()
    if len(data) > cap:
        return error(
            413, "too_large",
            f"That zip is larger than {svc.config.storage.max_upload_mb} MB.",
        )  # fmt: skip
    try:
        cand = await asyncio.to_thread(
            extract_zip, data, paper_id, svc.config.storage.workspace_dir, filename
        )
    except UploadError as exc:
        return error(400, "bad_zip", str(exc))
    return _use(svc, paper_id, cand)
