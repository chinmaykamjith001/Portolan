"""POST /api/papers/{id}/repo: link a found candidate, a local folder or a zip."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from portolan.api.app import Services
from portolan.models.repo import RepoCandidate, RepoStatus
from portolan.store.repos import chosen_repo, save_candidates
from tests.test_api_chart import ARXIV, wait_for
from tests.test_api_chart import client as client
from tests.test_api_chart import fake as fake

TINY = Path(__file__).parent / "fixtures" / "repos" / "tiny_transformer"


def _paper(client: TestClient) -> tuple[Services, str]:
    svc: Services = client.app.state.services  # type: ignore[attr-defined]
    paper_id = client.post("/api/papers", json={"input": ARXIV}).json()["paper_id"]
    # Let the background repo search finish first, so it can't overwrite what a test links.
    wait_for(lambda: (j := svc.queue.get(f"repos:{paper_id}")) is not None and j.state == "done")
    return svc, paper_id


def test_local_folder_is_linked_and_indexed(client: TestClient) -> None:
    svc, paper_id = _paper(client)
    r = client.post(f"/api/papers/{paper_id}/repo", json={"local_path": str(TINY)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "indexing"
    repo = chosen_repo(svc.db, paper_id)
    assert repo is not None and repo.local_path == str(TINY.resolve())
    wait_for(lambda: (j := svc.queue.get(f"index:{paper_id}")) is not None and j.state == "done")
    assert svc.db.fetchone("SELECT 1 AS x FROM code_indexes WHERE repo_id = ?", (repo.id,))


def test_zip_upload(client: TestClient) -> None:
    svc, paper_id = _paper(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in TINY.rglob("*.py"):
            z.write(f, f"tiny/{f.relative_to(TINY).as_posix()}")
    files = {"file": ("code.zip", buf.getvalue(), "application/zip")}
    r = client.post(f"/api/papers/{paper_id}/repo", files=files)
    assert r.status_code == 200, r.text
    repo = chosen_repo(svc.db, paper_id)
    assert repo is not None and repo.local_path and Path(repo.local_path).is_dir()


def test_candidate_choice_and_errors(client: TestClient) -> None:
    svc, paper_id = _paper(client)
    cand = RepoCandidate(
        id="gh:me/x", url="https://github.com/me/x", owner="me", name="x", source="paper_link",
        official=True, official_evidence=None, status="ok", host="github",
    )  # fmt: skip
    save_candidates(svc.db, paper_id, [cand], RepoStatus(kind="official_found", message="ok"))
    assert (
        client.post(f"/api/papers/{paper_id}/repo", json={"candidate_id": "gh:me/x"}).status_code
        == 200
    )
    repo = chosen_repo(svc.db, paper_id)
    assert repo is not None and repo.id == "gh:me/x"

    assert (
        client.post(f"/api/papers/{paper_id}/repo", json={"candidate_id": "nope"}).status_code
        == 404
    )
    assert client.post(f"/api/papers/{paper_id}/repo", json={}).status_code == 400
    bad = client.post(f"/api/papers/{paper_id}/repo", json={"local_path": "/no/such/dir"})
    assert bad.status_code == 400 and "folder" in bad.json()["error"]["message"]
    junk = {"file": ("code.zip", b"not a zip", "application/zip")}
    assert client.post(f"/api/papers/{paper_id}/repo", files=junk).status_code == 400
    assert client.post("/api/papers/arxiv:0000.00000v1/repo", json={}).status_code == 404
