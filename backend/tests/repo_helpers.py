"""Mocked GitHub / Hugging Face responses and tiny PaperDocs for repo-finder tests."""

from __future__ import annotations

from typing import Any

import httpx
import respx

from portolan.ingest.anchors import DocBuilder
from portolan.models.paper import PaperDoc, PaperRef

GH = "api.github.com"


def make_doc(
    *,
    abstract: str = "We study toy attention.",
    paragraphs: list[str] | None = None,
    links: list[str] | None = None,
    authors: list[str] | None = None,
    arxiv_id: str | None = "2101.00001",
    title: str = "Toy Attention",
) -> PaperDoc:
    b = DocBuilder()
    b.section(1, "1", "Introduction")
    for p in paragraphs or []:
        b.paragraph(p)
    for link in links or []:
        b.add_link(link)
    return b.build(
        paper_id=f"arxiv:{arxiv_id}v1" if arxiv_id else "doi:x",
        ref=PaperRef(arxiv_id=arxiv_id, version="v1" if arxiv_id else None),
        title=title,
        authors=authors or ["Ada Example", "Bo Sample"],
        abstract=abstract,
    )


def repo_json(
    full_name: str,
    *,
    stars: int = 10,
    owner_type: str = "User",
    fork: bool = False,
    pushed: str = "2024-01-01T00:00:00Z",
    branch: str = "main",
) -> dict[str, Any]:
    owner, name = full_name.split("/")
    return {
        "full_name": full_name,
        "name": name,
        "owner": {"login": owner, "type": owner_type},
        "html_url": f"https://github.com/{full_name}",
        "stargazers_count": stars,
        "pushed_at": pushed,
        "language": "Python",
        "default_branch": branch,
        "archived": False,
        "fork": fork,
        "description": "",
    }


def mock_repo(
    mock: respx.MockRouter,
    full_name: str,
    *,
    files: list[str] | None = None,
    readme: str | None = "# Toy\nSee arXiv 2101.00001.",
    display_name: str | None = None,
    requested_as: str | None = None,
    **info: Any,
) -> None:
    """Mock /repos, /git/trees, /readme and /users for one repository."""
    owner = full_name.split("/")[0]
    mock.get(host=GH, path=f"/repos/{requested_as or full_name}").mock(
        return_value=httpx.Response(200, json=repo_json(full_name, **info))
    )
    branch = info.get("branch", "main")
    files = ["README.md", "model.py", "train.py"] if files is None else files
    tree_route = mock.get(host=GH, path=f"/repos/{full_name}/git/trees/{branch}")
    if files:
        tree_route.mock(
            return_value=httpx.Response(
                200, json={"tree": [{"path": f, "type": "blob"} for f in files], "truncated": False}
            )
        )
    else:
        tree_route.mock(
            return_value=httpx.Response(409, json={"message": "Git Repository is empty."})
        )
    mock.get(host=GH, path=f"/repos/{full_name}/readme").mock(
        return_value=httpx.Response(200, text=readme) if readme is not None else httpx.Response(404)
    )
    mock.get(host=GH, path=f"/users/{owner}").mock(
        return_value=httpx.Response(200, json={"login": owner, "name": display_name})
    )


def mock_search(mock: respx.MockRouter, *repos: dict[str, Any]) -> respx.Route:
    return mock.get(host=GH, path="/search/repositories").mock(
        return_value=httpx.Response(200, json={"total_count": len(repos), "items": list(repos)})
    )


def mock_hf(mock: respx.MockRouter, arxiv_id: str = "2101.00001", **body: Any) -> respx.Route:
    route = mock.get(host="huggingface.co", path=f"/api/papers/{arxiv_id}")
    if body:
        return route.mock(return_value=httpx.Response(200, json={"id": arxiv_id, **body}))
    return route.mock(return_value=httpx.Response(404, json={"error": "Not found"}))
