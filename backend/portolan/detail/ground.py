"""Code grounding for a concept (spec §10.7.2)."""

from __future__ import annotations

from portolan.llm.gateway import Gateway
from portolan.models.detail import CodeGrounding
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore
from portolan.store.db import Database


async def ground_code(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, db: Database, concept_id: str
) -> CodeGrounding:
    """Retrieve spans for the concept, ask for line notes, validate them against the index.

    No repo linked or nothing found -> `grounding="concept_only"` (never an error).
    """
    raise NotImplementedError("code grounding arrives with M6")
