"""Clone and index a paper's chosen repo once, so the Code tab has something to ground against."""

from __future__ import annotations

import asyncio

from portolan.code.index_store import IndexStore
from portolan.code.indexer import index_checkout
from portolan.config import Config
from portolan.models.repo import RepoCandidate
from portolan.repo.cloner import CloneError, clone
from portolan.store.db import Database


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
    try:
        checkout = await asyncio.to_thread(clone, repo, config.storage.workspace_dir, paper_id)
    except CloneError:
        return False
    await asyncio.to_thread(index_checkout, store, checkout.root, repo.id, checkout.commit_sha)
    return True
