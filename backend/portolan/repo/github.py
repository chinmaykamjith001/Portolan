"""Minimal GitHub REST client over PoliteClient (cached, polite).

Unauthenticated GitHub allows ~60 requests/hour (search: 10/minute). `GITHUB_TOKEN` from `.env`
raises that; it is sent only to api.github.com and never stored or logged.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from portolan.ingest.http import FetchResult, PoliteClient

API = "https://api.github.com"
TTL = 24 * 3600.0
SEARCH_TTL = 24 * 3600.0


class GitHubRateLimited(Exception):
    def __init__(self) -> None:
        super().__init__(
            "GitHub's rate limit for anonymous requests is used up. "
            "Add a GITHUB_TOKEN to your .env (no scopes needed), or try again in an hour."
        )


@dataclass
class RepoInfo:
    full_name: str
    owner: str
    owner_type: str  # "User" | "Organization"
    name: str
    html_url: str
    stars: int
    pushed_at: datetime | None
    language: str | None
    default_branch: str
    archived: bool
    fork: bool
    description: str


@dataclass
class Tree:
    paths: list[str]
    truncated: bool
    empty: bool = False


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def repo_info(data: dict[str, Any]) -> RepoInfo:
    owner: dict[str, Any] = data.get("owner") or {}
    return RepoInfo(
        full_name=str(data["full_name"]),
        owner=str(owner.get("login", "")),
        owner_type=str(owner.get("type", "")),
        name=str(data["name"]),
        html_url=str(data.get("html_url") or f"https://github.com/{data['full_name']}"),
        stars=int(data.get("stargazers_count") or 0),
        pushed_at=parse_dt(data.get("pushed_at")),
        language=data.get("language"),
        default_branch=str(data.get("default_branch") or "main"),
        archived=bool(data.get("archived")),
        fork=bool(data.get("fork")),
        description=str(data.get("description") or ""),
    )


class GitHubClient:
    def __init__(self, http: PoliteClient, token: str | None = None) -> None:
        self.http = http
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "").strip()

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None, accept: str | None = None,
        ttl: float = TTL,
    ) -> FetchResult:  # fmt: skip
        result = await self.http.get(
            f"{API}{path}",
            params=params,
            headers=self._headers(accept) if accept else self._headers(),
            ttl=ttl,
        )
        if result.status in {403, 429} and (
            result.headers.get("x-ratelimit-remaining") == "0" or b"rate limit" in result.content
        ):
            raise GitHubRateLimited()
        return result

    async def repo(self, owner: str, name: str) -> RepoInfo | None:
        """None when the repo doesn't exist (or is private). Renames are followed."""
        result = await self._get(f"/repos/{quote(owner)}/{quote(name)}")
        if result.status != 200:
            return None
        return repo_info(json.loads(result.content))

    async def tree(self, info: RepoInfo) -> Tree | None:
        branch = quote(info.default_branch, safe="")
        result = await self._get(
            f"/repos/{info.full_name}/git/trees/{branch}", params={"recursive": "1"}
        )
        if result.status == 409:  # "Git Repository is empty."
            return Tree(paths=[], truncated=False, empty=True)
        if result.status != 200:
            return None
        body: dict[str, Any] = json.loads(result.content)
        entries: list[dict[str, Any]] = body.get("tree") or []
        return Tree(
            paths=[str(e["path"]) for e in entries if e.get("type") == "blob"],
            truncated=bool(body.get("truncated")),
        )

    async def readme(self, info: RepoInfo) -> str | None:
        result = await self._get(
            f"/repos/{info.full_name}/readme", accept="application/vnd.github.raw"
        )
        if result.status != 200:
            return None
        return result.content[:200_000].decode("utf-8", errors="replace")

    async def user_name(self, login: str) -> str | None:
        result = await self._get(f"/users/{quote(login)}")
        if result.status != 200:
            return None
        body: dict[str, Any] = json.loads(result.content)
        name = body.get("name")
        return str(name) if name else None

    async def search_readmes(self, phrase: str, limit: int = 10) -> list[RepoInfo]:
        clean = phrase.replace('"', " ").strip()
        result = await self._get(
            "/search/repositories",
            params={
                "q": f'"{clean}" in:readme',
                "sort": "stars",
                "order": "desc",
                "per_page": str(limit),
            },
            ttl=SEARCH_TTL,
        )
        if result.status != 200:
            return []
        body: dict[str, Any] = json.loads(result.content)
        items: list[dict[str, Any]] = body.get("items") or []
        return [repo_info(item) for item in items]
