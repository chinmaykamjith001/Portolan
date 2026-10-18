"""Shared setup for the verification tests: a hand-made paper, the planted toy repo, a scripted LLM.

The repo under tests/fixtures/verify/repo disagrees with the paper in three places:
  - dropout: the paper says 0.1, model.py sets DROPOUT = 0.3          (caught by literal_match)
  - decoder depth: the paper says 6 layers, Decoder builds range(4)    (caught by static)
  - attention scaling: the paper divides by sqrt(d_k), the code by d_k (needs the judge)
and five claims that agree (learning rate in another form, "eight" heads, batch size from YAML,
LayerNorm present, padding mask judged to match).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from portolan.code.index_store import IndexStore
from portolan.code.indexer import index_checkout
from portolan.config import Config, LimitsConfig, ModelsConfig
from portolan.ingest.anchors import DocBuilder
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from portolan.models.concept import Concept
from portolan.models.paper import PaperDoc, PaperRef
from portolan.models.repo import RepoCandidate
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.repos import choose_repo
from portolan.verify.claims import claim_key
from tests.conftest import FakeClock

FIXTURE = Path(__file__).parent / "fixtures" / "verify" / "repo"
REPO_ID = "github:ada-example/toy"
SHA = "b" * 40
PAPER_ID = "arxiv:2001.00001v1"


def paper() -> PaperDoc:
    b = DocBuilder()
    b.section(1, "1", "Introduction")
    b.paragraph("We propose Toy, a small attention model, and compare it with earlier work.")
    b.section(1, "3", "Model Architecture")
    b.section(2, "3.1", "Encoder and Decoder Stacks")
    b.paragraph("The decoder is composed of a stack of N = 6 identical layers in this model.")
    b.paragraph(
        "A residual connection is employed around each of the sub-layers, followed by layer "
        "normalization of the sum."
    )
    b.section(2, "3.2", "Attention")
    b.paragraph("The attention scores are scaled by 1/sqrt(d_k) before the softmax is applied.")
    b.paragraph(
        "Positions that are padding are masked out before the softmax is applied to scores."
    )
    b.paragraph(
        "We employ h = eight parallel attention heads, each with its own projection matrices."
    )
    b.section(1, "5", "Training")
    b.paragraph("We use a batch size of 64 sentence pairs and train for a fixed number of epochs.")
    b.paragraph(
        "We use the Adam optimizer with a learning rate of 0.001 for the whole training run."
    )
    b.paragraph("We apply dropout with a rate of 0.1 to the output of each sub-layer in the model.")
    return b.build(
        paper_id=PAPER_ID,
        ref=PaperRef(arxiv_id="2001.00001", version="v1"),
        title="Toy Attention",
        authors=["Ada Example"],
        abstract="We study a toy attention model.",
    )


def anchor_of(doc: PaperDoc, fragment: str) -> str:
    return next(a.id for a in doc.anchors if fragment in a.text)


# (fragment of the paper text, claim type, subject, value, short claim) in paper order.
CLAIMS: dict[str, tuple[str, str, str, str | None, str]] = {
    "decoder": ("stack of N = 6", "architecture", "number of decoder layers", "6",
                "The decoder is a stack of 6 identical layers."),
    "layernorm": ("layer normalization of the sum", "architecture",
                  "layer normalization after sub-layers", None,
                  "Each sub-layer output is followed by layer normalization."),
    "scaling": ("scaled by 1/sqrt", "algorithm_step", "attention score scaling", None,
                "Attention scores are divided by sqrt(d_k) before the softmax."),
    "mask": ("Positions that are padding", "algorithm_step", "padding mask", None,
             "Padding positions are masked out before the softmax."),
    "heads": ("h = eight", "hyperparameter", "number of attention heads", "eight",
              "The model uses eight attention heads."),
    "batch": ("batch size of 64", "hyperparameter", "batch size", "64",
              "The batch size is 64."),
    "lr": ("learning rate of 0.001", "hyperparameter", "learning rate", "0.001",
           "The learning rate is 0.001."),
    "dropout": ("dropout with a rate", "hyperparameter", "dropout rate", "0.1",
                "Dropout with rate 0.1 is applied to each sub-layer output."),
}  # fmt: skip
PLANTED = ("decoder", "scaling", "dropout")
CORRECT = ("layernorm", "mask", "heads", "batch", "lr")


def claim_key_of(doc: PaperDoc, name: str) -> str:
    fragment, _type, subject, value, _text = CLAIMS[name]
    return claim_key(anchor_of(doc, fragment), subject, value)


def line_of(relative: str, needle: str) -> int:
    lines = (FIXTURE / relative).read_text().splitlines()
    return next(i for i, line in enumerate(lines, start=1) if needle in line)


def extract_reply(doc: PaperDoc, names: tuple[str, ...] | None = None) -> dict[str, Any]:
    return {
        "claims": [
            {
                "anchor": anchor_of(doc, CLAIMS[n][0]),
                "claim_type": CLAIMS[n][1],
                "subject": CLAIMS[n][2],
                "value": CLAIMS[n][3],
                "text": CLAIMS[n][4],
            }
            for n in names or tuple(CLAIMS)
        ]
    }


def judge_reply(doc: PaperDoc) -> dict[str, Any]:
    return {
        "verdicts": [
            {
                "key": claim_key_of(doc, "scaling"),
                "verdict": "mismatch",
                "confidence": 0.9,
                "paper_side": "scores are divided by sqrt(d_k)",
                "code_side": "attention divides by d_k, with no square root",
                "path": "toy/attention.py",
                "start_line": line_of("toy/attention.py", "/ d_k"),
                "end_line": line_of("toy/attention.py", "/ d_k"),
            },
            {
                "key": claim_key_of(doc, "mask"),
                "verdict": "matches",
                "confidence": 0.9,
                "paper_side": "padding positions are masked before the softmax",
                "code_side": "masked_fill sets padding scores to -1e9 before softmax",
                "path": "toy/attention.py",
                "start_line": line_of("toy/attention.py", "masked_fill"),
                "end_line": line_of("toy/attention.py", "masked_fill"),
            },
        ]
    }


def reverse_reply() -> dict[str, Any]:
    line = line_of("toy/train.py", "clip_grad_norm_")
    return {
        "items": [
            {
                "title": "Gradient clipping at 1.0",
                "evidence": "train.py clips the gradient norm to 1.0 before every optimizer "
                "step; the paper lists no clipping.",
                "path": "toy/train.py",
                "start_line": line,
                "end_line": line,
                "severity": "low",
                "confidence": 0.8,
            }
        ]
    }


class ScriptedVerify:
    """Replies per template id, in order; counts calls; can fail a template once."""

    name = "scripted-verify"
    uses_quota = False

    def __init__(self, **replies: list[dict[str, Any] | Exception]) -> None:
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls: dict[str, int] = {}
        self.prompts: dict[str, list[str]] = {}

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        template = ctx.template_id
        self.calls[template] = self.calls.get(template, 0) + 1
        self.prompts.setdefault(template, []).append(messages[-1].content)
        step = self.replies[template].pop(0)
        if isinstance(step, Exception):
            raise step
        return ProviderResponse(text=json.dumps(step), usage=Usage(), model="fake")

    @property
    def total(self) -> int:
        return sum(self.calls.values())


def full_script(doc: PaperDoc) -> ScriptedVerify:
    return ScriptedVerify(
        claims_extract=[extract_reply(doc)],
        claim_judge=[judge_reply(doc)],
        reverse_scan=[reverse_reply()],
    )


def setup_chart(db: Database) -> ChartStore:
    """Three concepts whose anchors cover the paper's sections (sec:3.1, sec:3.2, sec:5)."""
    chart = ChartStore(db)
    for cid, title, gist, anchors in (
        ("decoder-stack", "Decoder stack", "A stack of identical decoder layers.", ["sec:3.1"]),
        ("attention", "Scaled attention", "Scores are scaled before the softmax.", ["sec:3.2"]),
        ("training", "Training setup", "Optimizer, batch size, dropout.", ["sec:5"]),
    ):
        chart.upsert_concept(
            PAPER_ID,
            Concept(id=cid, canonical_name=cid, title=title, gist=gist, depth=1, anchors=anchors),
        )
    return chart


def link_repo(db: Database, *, index: bool = True) -> None:
    choose_repo(
        db,
        PAPER_ID,
        RepoCandidate(
            id=REPO_ID, url="https://github.com/ada-example/toy", owner="ada-example",
            name="toy", source="paper_link", official=True, official_evidence=None, status="ok",
        ),
    )  # fmt: skip
    if index:
        index_checkout(IndexStore(db), FIXTURE, REPO_ID, SHA)


def gateway(db: Database, provider: ScriptedVerify) -> Gateway:
    config = Config(
        llm={  # type: ignore[arg-type]
            "provider": "gemini",
            "models": ModelsConfig(fast="g/f", default="g/d", strong="g/s"),
            "limits": LimitsConfig(rpm=1000, rpd=100_000, tpm=10_000_000, p0_reserve_rpm=2),
        }
    )
    return Gateway(config, db, provider=provider, clock=FakeClock())
