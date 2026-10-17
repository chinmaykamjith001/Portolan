"""Real arXiv fetches for the development papers (spec Appendix E). Not run in CI.

Run with: uv run pytest -m network
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import fetch_and_parse
from portolan.ingest.resolver import resolve
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
    ("raw", "must_have_sections", "repo"),
    [
        ("https://arxiv.org/abs/1706.03762", ["Introduction", "Model Architecture"], None),
        (
            "https://arxiv.org/abs/2106.09685",
            ["Introduction", "Our Method"],
            "github.com/microsoft/LoRA",
        ),
    ],
)
async def test_development_papers_parse(
    client: PoliteClient, raw: str, must_have_sections: list[str], repo: str | None
) -> None:
    result = await resolve(raw, client)
    assert result.ref is not None
    doc = (await fetch_and_parse(result.ref, client)).doc

    titles = [a.text for a in doc.by_kind("section")]
    for wanted in must_have_sections:
        assert any(wanted.lower() in t.lower() for t in titles), titles
    top = [a for a in doc.by_kind("section") if a.level == 1 and not a.is_appendix]
    assert len(top) >= 5
    equations = doc.by_kind("equation")
    assert len(equations) >= 3
    assert any(e.id == "eq:1" for e in equations)
    assert all(e.text for e in equations)
    assert len(doc.by_kind("paragraph")) >= 30
    if repo:
        assert any(repo.lower() in u.lower() for u in doc.links_found)
