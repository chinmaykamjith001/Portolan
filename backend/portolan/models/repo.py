"""Code repositories (spec §9.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RepoSource = Literal["paper_link", "hf_papers", "pwc_archive", "github_search", "user"]
RepoHealth = Literal["ok", "placeholder", "dead", "unknown"]
RepoHost = Literal["github", "gitlab", "upload", "local"]


class RepoCandidate(BaseModel):
    id: str  # "github:owner/name", "gitlab:group/sub/name", "upload:<sha>", "local:<sha>"
    url: str
    owner: str
    name: str
    source: RepoSource
    official: bool | None  # None = unknown
    official_evidence: str | None  # e.g. "linked in the paper", "owner matches author"
    status: RepoHealth
    stars: int | None = None
    last_commit: datetime | None = None
    language: str | None = None
    # Added (docs/DECISIONS.md):
    host: RepoHost = "github"
    default_branch: str | None = None
    cites_paper: bool = False  # README mentions the arXiv id
    status_reason: str | None = None  # "only a README", "repository not found", ...
    local_path: str | None = None  # uploads and local folders


class RepoStatus(BaseModel):
    kind: Literal["official_found", "unofficial_only", "placeholder_only", "none"]
    message: str  # user-facing, plain: "Official code found: org/repo"
