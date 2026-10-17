"""Mock provider: replays recorded responses by template + inputs hash (spec §11.7).

A missing fixture is a clear error, never a silent fake.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from portolan.llm.providers.base import CallContext, FixtureStore
from portolan.llm.types import Message, MockFixtureMissing, ProviderResponse

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "llm"


class MockProvider:
    name = "mock"
    uses_quota = False

    def __init__(self, fixtures_dir: Path = DEFAULT_FIXTURES) -> None:
        self.store = FixtureStore(fixtures_dir)

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        schema: type[BaseModel] | None,
        ctx: CallContext,
    ) -> ProviderResponse:
        response = self.store.read(ctx)
        if response is None:
            raise MockFixtureMissing(ctx.template_id, ctx.template_version, ctx.inputs_hash)
        return response
