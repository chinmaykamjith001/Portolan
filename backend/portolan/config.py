"""Configuration: `portolan.toml` + environment (`.env`).

Precedence (highest first): environment variables, `portolan.toml`, built-in defaults.
The API key is only ever read from the environment and is never stored on the config object;
use `api_key_present()` to check for it.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from portolan.llm.types import Role

ProviderName = Literal["gemini", "ollama", "mock"]

PLACEHOLDER_MARK = "<"


class ModelsConfig(BaseModel):
    fast: str = "gemini/<flash-lite-model-id>"
    default: str = "gemini/<flash-model-id>"
    strong: str = "gemini/<flash-model-id>"

    def for_role(self, role: Role) -> str:
        return getattr(self, role)

    def placeholders(self) -> list[Role]:
        roles: list[Role] = ["fast", "default", "strong"]
        return [r for r in roles if PLACEHOLDER_MARK in self.for_role(r)]


class LimitsConfig(BaseModel):
    rpm: int = 8
    rpd: int = 1400
    tpm: int = 200_000
    p0_reserve_rpm: int = 2
    p0_reserve_rpd_fraction: float = 0.1


class LLMConfig(BaseModel):
    provider: ProviderName = "gemini"
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    ollama_base_url: str = "http://localhost:11434"


class GenerationConfig(BaseModel):
    skeleton_batch_size: int = 6
    max_depth: int = 8
    explanation_words: tuple[int, int] = (200, 400)
    mismatch_confidence_threshold: float = 0.7


class SandboxConfig(BaseModel):
    mode: Literal["process", "docker"] = "process"
    timeout_seconds: float = 5
    memory_mb: int = 256


class StorageConfig(BaseModel):
    home: Path = Path("~/.portolan")

    @property
    def home_dir(self) -> Path:
        return self.home.expanduser()

    @property
    def db_path(self) -> Path:
        return self.home_dir / "portolan.db"

    @property
    def workspace_dir(self) -> Path:
        return self.home_dir / "workspace"


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    config_path: Path | None = None  # where the toml was loaded from, if any


def repo_root() -> Path:
    """The repository root (parent of `backend/`)."""
    return Path(__file__).resolve().parents[2]


def find_config_file() -> Path | None:
    explicit = os.environ.get("PORTOLAN_CONFIG")
    if explicit:
        return Path(explicit)
    for candidate in (Path.cwd() / "portolan.toml", repo_root() / "portolan.toml"):
        if candidate.is_file():
            return candidate
    return None


def load_env() -> Path | None:
    """Load `.env` from the repo root (or cwd) into the process environment. Returns its path.

    Set PORTOLAN_NO_DOTENV=1 to skip (the test suite does, so tests never see a real key).
    """
    if os.environ.get("PORTOLAN_NO_DOTENV"):
        return None
    for candidate in (Path.cwd() / ".env", repo_root() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


def load_config(path: Path | None = None, *, env: bool = True) -> Config:
    if env:
        load_env()
    path = path or find_config_file()
    data: dict[str, object] = {}
    if path is not None:
        with path.open("rb") as f:
            data = tomllib.load(f)
    config = Config.model_validate(data)
    config.config_path = path

    provider = os.environ.get("PORTOLAN_LLM_PROVIDER")
    if provider:
        config.llm.provider = LLMConfig.model_validate({"provider": provider}).provider
    ollama_url = os.environ.get("OLLAMA_BASE_URL")
    if ollama_url:
        config.llm.ollama_base_url = ollama_url
    home = os.environ.get("PORTOLAN_HOME")
    if home:
        config.storage.home = Path(home)
    return config


def api_key_present(provider: ProviderName) -> bool:
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY", "").strip())
    return True
