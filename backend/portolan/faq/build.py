"""FAQ (spec §10.10)."""

from __future__ import annotations

from collections.abc import Callable

from portolan.ingest.http import PoliteClient
from portolan.llm.gateway import Gateway
from portolan.models.finding import FAQItem
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore
from portolan.store.db import Database


async def build_faq(
    doc: PaperDoc,
    gateway: Gateway,
    chart: ChartStore,
    db: Database,
    http: PoliteClient,
    on_item: Callable[[FAQItem], None],
) -> None:
    """Collect from reviewer discussion, GitHub issues, findings and anticipated questions;
    cluster; write grounded answers. Saves each item through FaqStore and calls `on_item`.
    Every source is optional and skipped silently when unavailable. P2 priority."""
    raise NotImplementedError("the FAQ arrives with M9")


async def answer_question(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, db: Database, question: str
) -> FAQItem:
    """The Ask box: retrieval over anchors, code spans and collected sources, then a cited answer
    (P0). If the sources don't support an answer, say so (`supported=False`)."""
    raise NotImplementedError("the FAQ arrives with M9")
