"""M4 acceptance: replay recorded Gemini fixtures for the development papers (spec §18 M4).

The paper itself is fetched from arXiv (network), but every LLM answer comes from
backend/tests/fixtures/llm via the mock provider, so this costs zero LLM requests.
Run with: uv run pytest -m network tests/test_network_chart_replay.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from portolan.chart.canonical import canonical_name
from portolan.chart.master import GENERIC_SECTION_WORDS, ChartBuilder
from portolan.config import Config, ModelsConfig
from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import fetch_and_parse
from portolan.ingest.resolver import resolve
from portolan.llm.gateway import Gateway
from portolan.llm.providers.mock import MockProvider
from portolan.models.concept import MASTER
from portolan.store.chart import ChartStore
from portolan.store.db import Database, open_database

pytestmark = pytest.mark.network


@pytest.fixture
async def setup(tmp_path: Path) -> AsyncIterator[tuple[Database, PoliteClient]]:
    db = open_database(tmp_path / "replay.db")
    client = PoliteClient(db)
    yield db, client
    await client.aclose()
    db.close()


@pytest.mark.parametrize("paper", ["2106.09685", "1706.03762"])
async def test_recorded_chart_replays(setup: tuple[Database, PoliteClient], paper: str) -> None:
    db, client = setup
    result = await resolve(paper, client)
    assert result.ref is not None
    doc = (await fetch_and_parse(result.ref, client)).doc

    config = Config()
    config.llm.provider = "mock"
    # Same model ids as the recording (the cache key includes them; fixtures don't).
    config.llm.models = ModelsConfig(
        fast="gemini/gemini-3.5-flash-lite",
        default="gemini/gemini-3.8-flash",
        strong="gemini/gemini-3.8-flash",
    )
    gateway = Gateway(config, db, provider=MockProvider())
    store = ChartStore(db)
    builder = ChartBuilder(doc, gateway, store)
    master = await builder.master()
    await builder.ensure_frontier(MASTER, levels=2)

    concepts = store.concepts(doc.paper_id, master.concepts)
    titles = [c.title for c in concepts.values()]
    print(f"\n{doc.title}: {titles}")
    assert 10 <= len(master.concepts) <= 20
    sections = GENERIC_SECTION_WORDS  # structural names only (see chart.master._section_names)
    assert not [t for t in titles if canonical_name(t) in sections], "concepts must not be sections"
    assert len(master.links) >= len(master.concepts) // 2
    assert all(link.label.strip() for link in master.links)

    # Two levels of skeletons exist below every decomposable master concept.
    for cid, concept in concepts.items():
        if concept.is_first_principle:
            continue
        child = store.get_layer(doc.paper_id, cid)
        assert child is not None and len(child.concepts) >= 3, cid
        for gid in child.concepts:
            grand = store.get_concept(doc.paper_id, gid)
            assert grand is not None
            if not grand.is_first_principle and grand.depth == 2:
                assert store.get_layer(doc.paper_id, gid) is not None, gid
    assert gateway.limiter.budget.today() == []  # the mock never touches the real budget
