"""Portolan's internal evaluation records.

The real SciCoQA file format is UNVERIFIED (see eval/README.md): everything outside `adapters.py`
works on these types only, so adapting to the official format means editing one module.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field

Split = Literal["real", "synthetic", "unknown"]


class Discrepancy(BaseModel):
    """One gold paper-vs-code discrepancy."""

    id: str
    description: str
    code_file: str | None = None  # repo-relative path
    code_start: int | None = None  # 1-based, inclusive
    code_end: int | None = None
    paper_location: str | None = None  # "§3.2", "Eq. 4", free text
    category: str | None = None  # the dataset's own label, if any
    split: Split = "unknown"


class EvalRecord(BaseModel):
    """One paper and the repository it is compared against, with its gold discrepancies."""

    id: str
    paper: str  # arXiv id/URL, DOI or title: anything Portolan's resolver accepts
    repo_url: str
    repo_commit: str | None = None
    split: Split = "unknown"
    gold: list[Discrepancy] = Field(default_factory=list[Discrepancy])


def slug(text: str, limit: int = 24) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[-limit:] or "x"


def record_id(paper: str, repo_url: str, commit: str | None, split: str) -> str:
    """Stable across runs and machines: readable tail plus a hash of the full key."""
    key = "\0".join([paper.strip(), repo_url.strip().rstrip("/"), commit or "", split])
    return f"{slug(paper)}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
