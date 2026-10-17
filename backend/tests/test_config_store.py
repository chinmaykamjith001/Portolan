from __future__ import annotations

from pathlib import Path

import pytest

from portolan.config import api_key_present, load_config
from portolan.store.db import open_database
from portolan.store.migrations import MIGRATIONS


def test_defaults_without_files() -> None:
    config = load_config(env=False)
    assert config.config_path is None
    assert config.llm.limits.rpm == 8
    assert config.llm.models.placeholders() == ["fast", "default", "strong"]


def test_toml_and_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "portolan.toml"
    toml.write_text(
        '[llm]\nprovider = "gemini"\n[llm.models]\nfast = "gemini/a"\ndefault = "gemini/b"\n'
        'strong = "gemini/b"\n[llm.limits]\nrpm = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PORTOLAN_LLM_PROVIDER", "mock")
    config = load_config(env=False)
    assert config.config_path == toml
    assert config.llm.provider == "mock"
    assert config.llm.limits.rpm == 3
    assert config.llm.models.for_role("default") == "gemini/b"
    assert config.llm.models.placeholders() == []


def test_example_toml_parses() -> None:
    example = Path(__file__).resolve().parents[2] / "portolan.toml.example"
    config = load_config(example, env=False)
    assert config.generation.max_depth == 8
    assert config.sandbox.timeout_seconds == 5


def test_api_key_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not api_key_present("gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "  ")
    assert not api_key_present("gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert api_key_present("gemini")
    assert api_key_present("mock")


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    db = open_database(path)
    assert db.schema_version() == MIGRATIONS[-1][0]
    db.close()
    db = open_database(path)
    assert db.migrate() == MIGRATIONS[-1][0]
    assert db.fetchone("SELECT COUNT(*) FROM llm_cache")[0] == 0  # type: ignore[index]
    db.close()
