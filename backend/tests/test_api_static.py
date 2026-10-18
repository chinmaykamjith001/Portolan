"""One process serves the API and the built frontend; /api never falls through to the SPA."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portolan.api.app import build_services, create_app
from portolan.config import Config
from portolan.llm.providers.mock import MockProvider


def build_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Portolan app</title>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    (dist / "favicon.svg").write_text("<svg/>")
    (root / "secret.txt").write_text("outside the dist folder")
    return dist


def make_app(tmp_path: Path, static_dir: Path | None) -> TestClient:
    config = Config()
    config.llm.provider = "mock"
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    svc.gateway.provider = MockProvider()
    return TestClient(create_app(config, services=svc, static_dir=static_dir))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_app(tmp_path, build_dist(tmp_path)) as c:
        yield c


def test_root_and_spa_routes_get_index(client: TestClient) -> None:
    for path in ("/", "/some/client/route", "/index.html"):
        r = client.get(path)
        assert r.status_code == 200 and "Portolan app" in r.text, path


def test_assets_and_top_level_files(client: TestClient) -> None:
    assert client.get("/assets/app.js").text == "console.log('app')"
    assert client.get("/favicon.svg").text == "<svg/>"


def test_api_still_works_and_unknown_api_paths_are_json(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"
    assert "Portolan app" not in r.text


def test_path_traversal_does_not_leave_the_dist_folder(client: TestClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        r = client.get(path)
        assert "outside the dist folder" not in r.text, path


def test_no_static_dir_means_no_frontend(tmp_path: Path) -> None:
    with make_app(tmp_path, None) as c:
        assert c.get("/").status_code in (404, 405)
        assert c.get("/api/health").status_code == 200


def test_a_folder_without_index_is_ignored(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with make_app(tmp_path, empty) as c:
        assert c.get("/").status_code in (404, 405)
