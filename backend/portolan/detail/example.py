"""Worked examples whose numbers come from the sandbox (spec §10.7.3)."""

from __future__ import annotations

from portolan.llm.gateway import Gateway
from portolan.models.detail import Example
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore


async def make_example(
    doc: PaperDoc,
    gateway: Gateway,
    chart: ChartStore,
    concept_id: str,
    variant: int,
    previous: list[Example],
) -> Example:
    """Ask for a program, run it in the sandbox, keep only executed output.

    `variant` > 0 asks for a different case (preferably an edge case) than `previous`.
    """
    raise NotImplementedError("examples arrive with M6")
