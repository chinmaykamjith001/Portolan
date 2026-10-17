"""Real repo finding for the development papers (spec Appendix E). Not run in CI.

Runs anonymously against GitHub (about 30 of the 60 requests/hour per run).
Run with: uv run pytest -m network tests/test_network_repos.py -s
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import fetch_and_parse
from portolan.ingest.resolver import resolve
from portolan.repo.finder import RepoFinder
from portolan.store.db import Database, open_database

pytestmark = pytest.mark.network


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[PoliteClient]:
    db: Database = open_database(tmp_path / "net.db")
    c = PoliteClient(db)
    yield c
    await c.aclose()
    db.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://arxiv.org/abs/1706.03762", "github:tensorflow/tensor2tensor"),
        ("https://arxiv.org/abs/2106.09685", "github:microsoft/LoRA"),
    ],
)
async def test_development_papers_find_official_repo(
    client: PoliteClient, raw: str, expected: str
) -> None:
    result = await resolve(raw, client)
    assert result.ref is not None
    doc = (await fetch_and_parse(result.ref, client)).doc

    found = await RepoFinder(client).find(doc)
    print(f"\n{raw}: {found.status.kind} - {found.status.message}")
    for c in found.candidates:
        print(f"  {c.id} official={c.official} ({c.official_evidence}) {c.status} stars={c.stars}")
    for w in found.warnings:
        print(f"  warning: {w}")

    assert found.status.kind == "official_found", found.warnings
    best = found.best
    assert best is not None
    assert best.id == expected
    assert best.official is True
