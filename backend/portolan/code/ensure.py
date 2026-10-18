"""Clone and index a paper's chosen repo once, so the Code tab has something to ground against."""

from __future__ import annotations

import asyncio
import time

from portolan.code.index_store import IndexStore
from portolan.code.indexer import index_checkout
from portolan.config import Config
from portolan.models.repo import RepoCandidate
from portolan.repo.cloner import CloneError, clone
from portolan.store.db import Database

CLONE_RETRY_SECONDS = 600.0
_recent_failures: dict[str, float] = {}  # repo id -> when its clone last failed


async def ensure_index(config: Config, db: Database, paper_id: str, repo: RepoCandidate) -> bool:
    """True when the repo is indexed afterwards. Clone failures are logged by the caller's job
    state, not raised: a paper whose code can't be fetched still gets Chart and Route."""
    store = IndexStore(db)
    row = db.fetchone(
        "SELECT commit_sha FROM code_indexes WHERE repo_id = ? ORDER BY rowid DESC LIMIT 1",
        (repo.id,),
    )
    if row is not None and store.has_index(repo.id, row["commit_sha"]):
        return True
    failed_at = _recent_failures.get(repo.id)
    if failed_at is not None and time.monotonic() - failed_at < CLONE_RETRY_SECONDS:
        return False  # each opened concept would otherwise clone again (git can take minutes)
    try:
        checkout = await asyncio.to_thread(clone, repo, config.storage.workspace_dir, paper_id)
    except CloneError:
        _recent_failures[repo.id] = time.monotonic()
        return False
    _recent_failures.pop(repo.id, None)
    await asyncio.to_thread(index_checkout, store, checkout.root, repo.id, checkout.commit_sha)
    return True
