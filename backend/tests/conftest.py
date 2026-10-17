from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from portolan.config import Config, LimitsConfig, ModelsConfig
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderRateLimited, ProviderResponse, Usage
from portolan.store.db import Database, open_database


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never see the developer's real key, config or home directory."""
    for var in ("GEMINI_API_KEY", "GITHUB_TOKEN", "PORTOLAN_LLM_PROVIDER", "PORTOLAN_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PORTOLAN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PORTOLAN_NO_DOTENV", "1")
    # The owner's real portolan.toml / .env live at the repo root; tests must never read them.
    monkeypatch.setattr("portolan.config.repo_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def db() -> Iterator[Database]:
    database = open_database(":memory:")
    yield database
    database.close()


@pytest.fixture
def config() -> Config:
    return Config(
        llm={  # type: ignore[arg-type]
            "provider": "gemini",
            "models": ModelsConfig(
                fast="gemini/test-flash-lite",
                default="gemini/test-flash",
                strong="gemini/test-flash",
            ),
            "limits": LimitsConfig(rpm=4, rpd=100, tpm=100_000, p0_reserve_rpm=2),
        }
    )


class FakeClock:
    """Deterministic clock: sleeping advances time instantly."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds
        await asyncio.sleep(0)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


Step = ProviderResponse | Exception | Callable[[], ProviderResponse]


class ScriptedProvider:
    """A provider that returns pre-scripted responses (or raises) in order."""

    name = "scripted"
    uses_quota = True

    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, list[Message], CallContext]] = []

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        schema: type[BaseModel] | None,
        ctx: CallContext,
    ) -> ProviderResponse:
        self.calls.append((model, messages, ctx))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step()
        return step


def ok(
    text: str, model: str = "gemini/test-flash", tokens: tuple[int, int] = (10, 5)
) -> ProviderResponse:
    return ProviderResponse(
        text=text, usage=Usage(input_tokens=tokens[0], output_tokens=tokens[1]), model=model
    )


def rate_limited(retry_after: float | None = None) -> ProviderRateLimited:
    return ProviderRateLimited(retry_after=retry_after)
