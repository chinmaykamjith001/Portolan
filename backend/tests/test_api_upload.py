"""PDF upload through `POST /api/papers` (multipart), with the fake chart LLM from the M4 tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from portolan.ingest.pipeline import upload_paper_id
from tests import test_api_chart
from tests.ingest_helpers import synthetic_pdf
from tests.test_api_chart import wait_for

# Same app, fake LLM and mocked arXiv as the M4 API tests.
client = test_api_chart.client
fake = test_api_chart.fake


def upload(client: TestClient, data: bytes, name: str = "paper.pdf") -> tuple[int, dict]:  # type: ignore[type-arg]
    r = client.post("/api/papers", files={"file": (name, data, "application/pdf")})
    return r.status_code, r.json()


def test_upload_ingests_and_builds_the_map(client: TestClient) -> None:
    pdf = synthetic_pdf()
    status, body = upload(client, pdf)
    assert status == 202 and body == {"paper_id": upload_paper_id(pdf), "status": "queued"}
    paper_id = body["paper_id"]
    assert paper_id.startswith("upload:")

    info = wait_for(
        lambda: (r := client.get(f"/api/papers/{paper_id}")).status_code == 200 and r.json()
    )
    assert info["status"] == "ready" and info["low_structure"] is True
    assert info["source_kind"] == "upload"
    wait_for(lambda: client.get(f"/api/papers/{paper_id}/chart/master").status_code == 200)

    svc = client.app.state.services  # type: ignore[attr-defined]
    stored = svc.config.storage.uploads_dir / f"{paper_id.split(':', 1)[1]}.pdf"
    assert stored.read_bytes() == pdf

    # The same file again is the same paper, already read.
    assert upload(client, pdf, "renamed.pdf") == (200, {"paper_id": paper_id, "status": "ready"})


def test_upload_rejects_what_is_not_a_pdf(client: TestClient) -> None:
    status, body = upload(client, b"<html>not a pdf</html>", "notes.html")
    assert status == 400 and body["error"]["code"] == "not_pdf"
    assert "notes.html doesn't look like a PDF" in body["error"]["message"]

    status, body = upload(client, b"", "empty.pdf")
    assert status == 400 and body["error"]["code"] == "not_pdf"


def test_upload_size_cap(client: TestClient) -> None:
    svc = client.app.state.services  # type: ignore[attr-defined]
    svc.config.storage.max_upload_mb = 0
    status, body = upload(client, synthetic_pdf())
    assert status == 413 and body["error"]["code"] == "too_large"
    assert "larger than 0 MB" in body["error"]["message"]


def test_upload_needs_a_file_field(client: TestClient) -> None:
    r = client.post("/api/papers", data={"input": "x"}, files={"other": ("a.pdf", b"%PDF-1.4")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_upload"


def test_json_body_errors_unchanged(client: TestClient) -> None:
    json_type = {"content-type": "application/json"}
    r = client.post("/api/papers", content=b"not json", headers=json_type)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"
    r = client.post("/api/papers", json={"input": 3})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"
