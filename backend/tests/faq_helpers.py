"""Shared fixtures for the FAQ tests: mocked OpenReview / GitHub payloads, a paper and a chart."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import respx
from pydantic import BaseModel

from portolan.config import Config, LimitsConfig, ModelsConfig
from portolan.ingest.anchors import DocBuilder
from portolan.ingest.http import PoliteClient
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderError, ProviderResponse, Usage
from portolan.models.concept import Concept
from portolan.models.finding import Finding
from portolan.models.paper import PaperDoc, PaperRef
from portolan.models.repo import RepoCandidate
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from tests.conftest import FakeClock
from tests.detail_helpers import PAPER_ID

OR = "https://api2.openreview.net"
GH = "https://api.github.com"
TITLE = "Attention Is All You Need"
FORUM = "FORUM1"
Q1 = "Why are the dot products divided by the square root of the key size?"
Q2 = "How many attention heads were used in the base experiments reported?"
Q3 = "Does the model use any recurrence anywhere in the encoder stack?"


def http(db: Database) -> PoliteClient:
    hosts = ("api2.openreview.net", "api.github.com", "openreview.net")
    return PoliteClient(db, max_retries=0, spacing={h: 0.0 for h in hosts})


def repo(host: str = "github") -> RepoCandidate:
    return RepoCandidate(
        id="github:example/transformer", url="https://github.com/example/transformer",
        owner="example", name="transformer", source="paper_link", official=True,
        official_evidence=None, status="ok", host=host,  # type: ignore[arg-type]
    )  # fmt: skip


def concepts(chart: ChartStore) -> None:
    for cid, title, anchors in (
        ("scaled-dot", "Scaled dot-product", ["eq:1"]),
        ("multi-head", "Multi-head attention", ["sec:3.2"]),
        ("positional", "Positional encoding", ["eq:1"]),
    ):
        chart.upsert_concept(
            PAPER_ID,
            Concept(
                id=cid, canonical_name=title.lower(), title=title, gist=f"{title} in brief.",
                depth=1, anchors=anchors, has_layer=True, grounding="concept_only",
            ),
        )  # fmt: skip


def finding(verdict: str = "mismatch", fid: str = "f1") -> Finding:
    return Finding(
        id=fid, claim_text="a dropout rate of 0.1.", claim_type="hyperparameter",
        paper_anchor="eq:1", verdict=verdict, severity="medium", confidence=0.9,  # type: ignore[arg-type]
        method="literal_match", evidence="The paper says 0.1; the code sets 0.3.",
        concept_ids=["scaled-dot"],
    )  # fmt: skip


def search_payload(title: str = TITLE) -> dict[str, Any]:
    return {"notes": [{"id": FORUM, "forum": FORUM, "content": {"title": {"value": title}}}]}


def forum_payload(*, wrapped: bool = True) -> dict[str, Any]:
    def c(**fields: str) -> dict[str, Any]:
        return {k: ({"value": v} if wrapped else v) for k, v in fields.items()}

    inv = "Conf/2024/Submission1/-/"
    return {
        "notes": [
            {"id": FORUM, "forum": FORUM, "content": c(title=TITLE)},
            {
                "id": "R1", "forum": FORUM, "replyto": FORUM,
                "invitations": [inv + "Official_Review"],
                "signatures": ["Conf/2024/Submission1/Reviewer_x"],
                "content": c(questions=f"1. {Q1}\n2. {Q2}\n- Is this fine?"),
            },
            {
                "id": "A1", "forum": FORUM, "replyto": "R1",
                "invitations": [inv + "Official_Comment"],
                "signatures": ["Conf/2024/Submission1/Authors"],
                "content": c(comment="We divide by the root of d_k so scores keep unit variance."),
            },
            {
                "id": "R2", "forum": FORUM, "replyto": FORUM,
                "invitations": [inv + "Official_Review"],
                "signatures": ["Conf/2024/Submission1/Reviewer_y"],
                "content": c(questions=Q3),
            },
        ]
    }  # fmt: skip


def mock_openreview(mock: respx.MockRouter, *, search: Any = None, forum: Any = None) -> None:
    mock.get(f"{OR}/notes/search").mock(
        return_value=httpx.Response(200, json=search if search is not None else search_payload())
    )
    mock.get(f"{OR}/notes").mock(
        return_value=httpx.Response(200, json=forum if forum is not None else forum_payload())
    )


def issue(number: int, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "number": number, "title": title, "body": "Details here.", "comments": 4,
        "html_url": f"https://github.com/example/transformer/issues/{number}",
        "labels": [], "reactions": {"total_count": 1}, **extra,
    }  # fmt: skip


def mock_issues(mock: respx.MockRouter, issues: list[dict[str, Any]] | None = None) -> None:
    base = f"{GH}/repos/example/transformer/issues"
    listing = issues if issues is not None else [
        issue(7, "Why is the learning rate warmed up?", comments=9),
        issue(8, "Fix typo in README", comments=3),                        # not a question
        issue(9, "How do I run this on two GPUs?", pull_request={"url": "x"}),  # a PR
        issue(10, "What does label smoothing do here?", comments=0),       # no replies
        issue(11, "Is dropout applied to the embeddings?", comments=2),
    ]  # fmt: skip
    mock.get(base).mock(return_value=httpx.Response(200, content=json.dumps(listing)))
    for n, body, assoc in (
        (7, "Warmup avoids early divergence with Adam.", "MEMBER"),
        (11, "Yes, with rate 0.1.", "NONE"),
    ):
        mock.get(f"{base}/{n}/comments").mock(
            return_value=httpx.Response(200, json=[{"body": body, "author_association": assoc}])
        )


# -- a paper whose paragraphs match the questions above, and a fake LLM for the FAQ templates ----


def faq_paper() -> PaperDoc:
    b = DocBuilder()
    b.section(1, "3", "Model Architecture")
    b.section(2, "3.2", "Attention")
    b.paragraph(
        "The scores are divided by the square root of d_k, the key size, so softmax is stable."
    )
    b.equation(r"\mathrm{softmax}(QK^T/\sqrt{d_k})V", "1")
    b.paragraph("We use eight parallel attention heads, each with a smaller projection.")
    b.paragraph("The learning rate warms up linearly for the first steps and then decays.")
    b.paragraph("Dropout is applied to the output of every sublayer with rate 0.1.")
    return b.build(
        paper_id=PAPER_ID,
        ref=PaperRef(arxiv_id="1706.03762", version="v7"),
        title=TITLE,
        authors=["A. Vaswani"],
        abstract="We propose the Transformer.",
    )


_PASSAGE_LINE = re.compile(r"(?m)^\[([^\]\s]+)\] (?!Q: )")
_FAQ_LINE = re.compile(r"(?m)^\[([^\]\s]+)\] Q: ")
_CODE_LINE = re.compile(r"(?m)^(\S+:\d+-\d+)  ")


class FakeFaqLLM:
    """Answers the three FAQ templates by citing the passage ids that appear in the prompt, plus
    an invented one that validation must drop. Records (template_id, user message) per call."""

    name = "fake"
    uses_quota = False

    def __init__(
        self,
        *,
        drop_cluster: int | None = None,  # faq_answer: this cluster gets keep=false
        invented_concept: int | None = None,  # faq_anticipated: this concept cites only made-up ids
        unsupported: bool = False,  # ask_answer: supported=false
        invented_only: bool = False,  # ask_answer: cites only made-up sources
        cites_nothing: bool = False,  # ask_answer: answers but leaves every citation list empty
        fail: bool = False,
    ) -> None:
        self.drop_cluster, self.invented_concept = drop_cluster, invented_concept
        self.unsupported, self.invented_only, self.fail = unsupported, invented_only, fail
        self.cites_nothing = cites_nothing
        self.calls: list[tuple[str, str]] = []

    def count(self, template_id: str) -> int:
        return sum(1 for t, _ in self.calls if t == template_id)

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        user = messages[-1].content
        self.calls.append((ctx.template_id, user))
        if self.fail:
            raise ProviderError("The provider is down.")
        body: dict[str, Any]
        if ctx.template_id == "faq_answer":
            items = []
            for part in re.split(r"(?m)^Cluster (?=\d+ \()", user)[1:]:
                n = int(part.split(" ", 1)[0])
                ids = _PASSAGE_LINE.findall(part)
                items.append(
                    {
                        "cluster": n,
                        "keep": n != self.drop_cluster,
                        "question": "Cleaned: " + re.search(r"Question: (.*)", part).group(1),  # type: ignore[union-attr]
                        "answer": "A grounded answer.",
                        "anchors": [*ids[:1], "made-up"],
                    }
                )
            body = {"items": [*items, {"cluster": 99, "question": "x", "answer": "y"}]}
        elif ctx.template_id == "faq_anticipated":
            items = []
            for part in re.split(r"(?m)^Concept (?=\d+:)", user)[1:]:
                n = int(part.split(":", 1)[0])
                ids = _PASSAGE_LINE.findall(part)
                items.append(
                    {
                        "concept": n,
                        "question": f"What should I know about concept {n}?",
                        "answer": "Because it carries the idea.",
                        "anchors": ["made-up"]
                        if n == self.invented_concept
                        else [*ids[:1], "made-up"],
                    }
                )
            body = {"items": items}
        else:  # ask_answer
            ids, faq = _PASSAGE_LINE.findall(user), _FAQ_LINE.findall(user)
            spans = _CODE_LINE.findall(user)
            body = {
                "supported": not self.unsupported,
                "answer": "" if self.unsupported else "Because the scores would otherwise grow.",
                "anchors": ["made-up"] if self.invented_only else ids[:1],
                "faq": [] if self.invented_only else faq[:1],
                "code": ["nope.py:1-2"] if self.invented_only else [*spans[:1], "nope.py:1-2"],
            }
            if self.cites_nothing:
                body.update(anchors=[], faq=[], code=[])
        return ProviderResponse(text=json.dumps(body), usage=Usage(), model="fake")


def faq_gateway(db: Database, llm: FakeFaqLLM) -> Gateway:
    config = Config(
        llm={  # type: ignore[arg-type]
            "provider": "gemini",
            "models": ModelsConfig(fast="g/f", default="g/d", strong="g/s"),
            "limits": LimitsConfig(rpm=1000, rpd=100_000, tpm=10_000_000, p0_reserve_rpm=2),
        }
    )
    return Gateway(config, db, provider=llm, clock=FakeClock())
