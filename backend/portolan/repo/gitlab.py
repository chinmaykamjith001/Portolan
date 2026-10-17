"""Minimal GitLab (gitlab.com) REST client for validating paper-linked repos."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from portolan.ingest.http import PoliteClient
from portolan.repo.github import RepoInfo, Tree, parse_dt

API = "https://gitlab.com/api/v4"
TTL = 24 * 3600.0


class GitLabClient:
    def __init__(self, http: PoliteClient) -> None:
        self.http = http

    async def repo(self, full_path: str) -> tuple[int, RepoInfo] | None:
        result = await self.http.get(f"{API}/projects/{quote(full_path, safe='')}", ttl=TTL)
        if result.status != 200:
            return None
        d: dict[str, Any] = json.loads(result.content)
        namespace: dict[str, Any] = d.get("namespace") or {}
        path = str(d.get("path_with_namespace") or full_path)
        return int(d["id"]), RepoInfo(
            full_name=path,
            owner=path.rsplit("/", 1)[0],
            owner_type="Organization" if namespace.get("kind") == "group" else "User",
            name=str(d.get("path") or path.rsplit("/", 1)[-1]),
            html_url=str(d.get("web_url") or f"https://gitlab.com/{path}"),
            stars=int(d.get("star_count") or 0),
            pushed_at=parse_dt(d.get("last_activity_at")),
            language=None,
            default_branch=str(d.get("default_branch") or "main"),
            archived=bool(d.get("archived")),
            fork="forked_from_project" in d,
            description=str(d.get("description") or ""),
        )

    async def tree(self, project_id: int) -> Tree | None:
        result = await self.http.get(
            f"{API}/projects/{project_id}/repository/tree",
            params={"recursive": "true", "per_page": "100"},
            ttl=TTL,
        )
        if result.status == 404:
            return Tree(paths=[], truncated=False, empty=True)
        if result.status != 200:
            return None
        entries: list[dict[str, Any]] = json.loads(result.content)
        paths = [str(e["path"]) for e in entries if e.get("type") == "blob"]
        return Tree(paths=paths, truncated=len(entries) >= 100)
