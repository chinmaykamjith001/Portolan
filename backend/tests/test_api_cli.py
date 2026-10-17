from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from portolan.api.app import create_app
from portolan.cli import app as cli
from portolan.config import Config
from portolan.llm.providers.gemini import MODELS_URL, suggest_models

runner = CliRunner()


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.storage.home = tmp_path / "home"
    return config


def test_health_and_budget_endpoints(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.llm.provider = "mock"
    with TestClient(create_app(config)) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok" and health["provider"] == "mock"
        assert health["free_tier_notice"] is None
        budget = client.get("/api/budget").json()
        assert budget["requests_today"] == 0 and budget["provider"] == "mock"
        missing = client.get("/api/nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "http_error"


def test_health_never_exposes_the_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-test-value")
    with TestClient(create_app(_config(tmp_path))) as client:
        body = client.get("/api/health").text
    assert "secret-test-value" not in body
    assert "Free Gemini keys may let Google use what you send" in body


def test_doctor_without_key_explains_what_is_missing() -> None:
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert "GEMINI_API_KEY is not set" in result.output
    assert "aistudio.google.com" in result.output
    assert "placeholder" in result.output


def test_doctor_with_mock_provider_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTOLAN_LLM_PROVIDER", "mock")
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "Mock provider" in result.output


@respx.mock
def test_doctor_lists_models_and_suggests_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-test-value")
    (tmp_path / "portolan.toml").write_text(
        '[llm.models]\nfast = "gemini/a"\ndefault = "gemini/b"\nstrong = "gemini/b"\n',
        encoding="utf-8",
    )
    route = respx.get(MODELS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-9.0-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-9.0-flash-lite",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-9",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )
    )
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 0, result.output
    assert 'fast = "gemini/gemini-9.0-flash-lite"' in result.output
    assert "text-embedding-9" not in result.output
    assert "secret-test-value" not in result.output
    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == "secret-test-value"
    assert "secret-test-value" not in str(request.url)


def test_suggest_models_prefers_newest_stable() -> None:
    names = [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash-preview-05-20",
        "gemini-2.5-pro",
    ]
    assert suggest_models(names) == {
        "fast": "gemini/gemini-2.5-flash-lite",
        "default": "gemini/gemini-2.5-flash",
        "strong": "gemini/gemini-2.5-flash",
    }
    assert suggest_models([]) == {}
