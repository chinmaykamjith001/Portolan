"""Live check of the FAQ sources' external response shapes. Not run in CI.

The OpenReview v2 and GitHub parsing in portolan/faq/sources.py was written from their
documentation and covered with mocked responses only. Run this once on a machine with network
access to confirm the live shapes:  uv run pytest -m network tests/test_network_faq.py -s

LoRA (ICLR 2022) has a public OpenReview forum; Attention Is All You Need predates OpenReview, so
for it the reviewer source is expected to be empty.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from portolan.faq.sources import github_issue_items, reviewer_items
from portolan.ingest.http import PoliteClient
from portolan.models.repo import RepoCandidate
from portolan.store.db import Database, open_database

pytestmark = pytest.mark.network

LORA = "LoRA: Low-Rank Adaptation of Large Language Models"


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[PoliteClient]:
    db: Database = open_database(tmp_path / "net.db")
    c = PoliteClient(db)
    yield c
    await c.aclose()
    db.close()


async def test_openreview_forum_for_lora(client: PoliteClient) -> None:
    items = await reviewer_items(client, LORA)
    for i in items:
        print(f"\n{i.source_url}\n  Q: {i.question}\n  reply: {i.source_text[:120]!r}")
    # An empty list means the live shape differs from the documented one: inspect
    # https://api2.openreview.net/notes/search?term=LoRA&content=title&source=forum and fix
    # portolan/faq/sources.py (_val, _is_review, _is_author) accordingly.
    assert items, "no answered reviewer questions parsed from the LoRA forum"
    assert all(
        i.source_url and i.source_url.startswith("https://openreview.net/forum") for i in items
    )


async def test_openreview_has_nothing_for_a_pre_openreview_paper(client: PoliteClient) -> None:
    assert await reviewer_items(client, "Attention Is All You Need") == []


async def test_github_issue_questions_for_lora(client: PoliteClient) -> None:
    repo = RepoCandidate(
        id="github:microsoft/LoRA", url="https://github.com/microsoft/LoRA", owner="microsoft",
        name="LoRA", source="paper_link", official=True, official_evidence=None, status="ok",
    )  # fmt: skip
    items = await github_issue_items(client, repo)
    for i in items:
        print(f"\n{i.source_label}: {i.question}\n  {i.source_url}")
    assert items, "no question-like issues with replies parsed (or GitHub rate-limited you)"
    assert all(i.source_label.startswith("From GitHub issue #") for i in items)
