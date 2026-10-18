"""`GET /api/settings`: what Settings shows, and never a secret."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portolan.api.app import build_services, create_app
from portolan.config import Config
from portolan.llm.providers.mock import MockProvider

FAKE_KEY = "AIza-this-is-a-fake-key-for-tests-0123456789"


def make_client(tmp_path: Path, provider: str) -> TestClient:
    config = Config()
    config.llm.provider = provider  # type: ignore[assignment]
    config.storage.home = tmp_path / "home"
    svc = build_services(config)
    svc.gateway.provider = MockProvider()
    return TestClient(create_app(config, services=svc))


@pytest.fixture
def mock_client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path, "mock") as c:
        yield c


def test_settings_shape_in_mock_mode(mock_client: TestClient) -> None:
    body = mock_client.get("/api/settings").json()
    assert body["provider"] == "mock"
    assert body["free_tier_notice"] is False
    assert body["sandbox_mode"] == "process"
    assert body["repo_linked"] is False
    assert set(body["models"]) == {"fast", "default", "strong"}
    assert body["limits"] == {"rpm": 8, "rpd": 1400, "tpm": 200_000}
    assert body["version"]
    # Placeholder model ids are shown as written, so the UI can tell the user to run `doctor`.
    assert body["models"]["default"] == "gemini/<flash-model-id>"


def test_free_tier_notice_for_gemini(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    with make_client(tmp_path, "gemini") as c:
        body = c.get("/api/settings").json()
    assert body["provider"] == "gemini" and body["free_tier_notice"] is True


def test_settings_never_contains_a_key_or_env_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token_for_tests_0123456789")
    with make_client(tmp_path, "gemini") as c:
        text = c.get("/api/settings").text + c.get("/api/health").text + c.get("/api/budget").text
    assert FAKE_KEY not in text
    assert "ghp_fake_token" not in text
    assert "GEMINI_API_KEY" not in text
