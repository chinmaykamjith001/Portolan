"""Graph templates whose data comes from the sandbox (spec §10.7.4)."""

from __future__ import annotations

from portolan.llm.gateway import Gateway
from portolan.models.detail import GraphSpec
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore


async def make_graph(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, concept_id: str
) -> GraphSpec | None:
    """Pick a template and compute its data in the sandbox; `None` when no visual would help."""
    raise NotImplementedError("graphs arrive with M6")
