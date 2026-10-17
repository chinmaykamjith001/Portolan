"""Provider contract. Providers are only ever called by the gateway."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from portolan.llm.types import Message, ProviderResponse, Role


class CallContext(BaseModel):
    template_id: str
    template_version: int
    inputs_hash: str
    role: Role
    attempt: int = 0  # 0 = first try, 1 = the single schema-repair retry
    reasoning: str | None = None  # from PromptTemplate.reasoning


class Provider(Protocol):
    name: str
    uses_quota: bool  # False for the mock: no rate limiting or budget accounting

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        schema: type[BaseModel] | None,
        ctx: CallContext,
    ) -> ProviderResponse: ...


class FixtureStore:
    """Recorded responses on disk: `<root>/<template_id>/v<version>/<inputs_hash>[.retryN].json`.

    Fixtures hold only the response text, token usage and model id — never request headers.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, ctx: CallContext) -> Path:
        suffix = f".retry{ctx.attempt}" if ctx.attempt else ""
        return (
            self.root
            / ctx.template_id
            / f"v{ctx.template_version}"
            / f"{ctx.inputs_hash}{suffix}.json"
        )

    def read(self, ctx: CallContext) -> ProviderResponse | None:
        p = self.path(ctx)
        if not p.is_file():
            return None
        return ProviderResponse.model_validate_json(p.read_text(encoding="utf-8"))

    def write(self, ctx: CallContext, response: ProviderResponse) -> Path:
        p = self.path(ctx)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return p
