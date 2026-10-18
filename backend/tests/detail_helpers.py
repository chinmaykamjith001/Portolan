"""Shared setup for the example and graph unit tests: paper, concept, gateway, fake sandbox."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from portolan.config import Config, LimitsConfig, ModelsConfig
from portolan.ingest.anchors import DocBuilder
from portolan.llm.gateway import Gateway
from portolan.llm.types import ProviderResponse
from portolan.models.concept import Concept
from portolan.models.paper import PaperDoc, PaperRef
from portolan.sandbox import SandboxResult
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from tests.conftest import FakeClock, ScriptedProvider, ok

PAPER_ID = "arxiv:1706.03762v7"
CONCEPT_ID = "softmax"


def paper() -> PaperDoc:
    b = DocBuilder()
    b.section(1, "3", "Model Architecture")
    b.section(2, "3.2", "Attention")
    b.paragraph("We apply a softmax function to obtain the weights on the values.")
    b.equation(r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T/\sqrt{d_k})V", "1")
    return b.build(
        paper_id=PAPER_ID,
        ref=PaperRef(arxiv_id="1706.03762", version="v7"),
        title="Attention Is All You Need",
        authors=["A. Vaswani"],
        abstract="We propose the Transformer.",
    )


def store(db: Database) -> ChartStore:
    chart = ChartStore(db)
    chart.upsert_concept(
        PAPER_ID,
        Concept(
            id=CONCEPT_ID,
            canonical_name="softmax",
            title="Softmax",
            gist="Turns scores into positive weights that sum to 1.",
            depth=1,
            anchors=["eq:1"],
        ),
    )
    return chart


def gateway(db: Database, provider: ScriptedProvider) -> Gateway:
    config = Config(
        llm={  # type: ignore[arg-type]
            "provider": "gemini",
            "models": ModelsConfig(fast="g/f", default="g/d", strong="g/s"),
            "limits": LimitsConfig(rpm=1000, rpd=100_000, tpm=10_000_000, p0_reserve_rpm=2),
        }
    )
    return Gateway(config, db, provider=provider, clock=FakeClock())


def reply(**fields: Any) -> ProviderResponse:
    return ok(json.dumps(fields))


def good(output: object = None, *, stdout: str = "") -> SandboxResult:
    return SandboxResult(ok=True, output=output, stdout=stdout, error=None, duration_s=0.01)


def failed(error: str) -> SandboxResult:
    return SandboxResult(ok=False, output=None, stdout="", error=error, duration_s=0.01)


class FakeSandbox:
    """Scripted `run_program` replacement that records the sources it was asked to run."""

    def __init__(self, results: list[SandboxResult | Callable[[str], SandboxResult]]) -> None:
        self.results: list[Any] = list(results)
        self.sources: list[str] = []

    async def __call__(self, source: str, **_: Any) -> SandboxResult:
        self.sources.append(source)
        step = self.results.pop(0)
        return step if isinstance(step, SandboxResult) else step(source)
