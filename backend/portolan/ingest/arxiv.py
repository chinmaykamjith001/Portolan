"""arXiv API (Atom) metadata and search. Endpoint: export.arxiv.org/api/query."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from typing import Any

from portolan.ingest.http import FetchError, PoliteClient

# lxml ships no type stubs; keep its untyped surface in this module.
etree: Any = importlib.import_module("lxml.etree")

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
SEARCH_TTL = 24 * 3600.0
METADATA_TTL_UNVERSIONED = 24 * 3600.0

_ABS_ID = re.compile(r"arxiv\.org/abs/(?P<id>.+?)(?P<version>v\d+)?$")


@dataclass
class ArxivEntry:
    arxiv_id: str
    version: str | None
    title: str
    authors: list[str] = field(default_factory=list[str])
    abstract: str = ""
    published: str = ""


def _safe_parser() -> Any:
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _text(el: Any) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(str(t) for t in el.itertext())).strip()


def parse_feed(content: bytes) -> list[ArxivEntry]:
    root: Any = etree.fromstring(content, parser=_safe_parser())
    entries: list[ArxivEntry] = []
    for entry in root.iter(f"{ATOM}entry"):
        m = _ABS_ID.search(_text(entry.find(f"{ATOM}id")))
        if not m:
            continue  # error entries have no abs id
        entries.append(
            ArxivEntry(
                arxiv_id=m.group("id"),
                version=m.group("version"),
                title=_text(entry.find(f"{ATOM}title")),
                authors=[_text(a.find(f"{ATOM}name")) for a in entry.iter(f"{ATOM}author")],
                abstract=_text(entry.find(f"{ATOM}summary")),
                published=_text(entry.find(f"{ATOM}published")),
            )
        )
    return entries


async def fetch_metadata(
    client: PoliteClient, arxiv_id: str, version: str | None
) -> ArxivEntry | None:
    vid = f"{arxiv_id}{version or ''}"
    result = await client.get(
        API_URL,
        params={"id_list": vid},
        ttl=None if version else METADATA_TTL_UNVERSIONED,
    )
    if not result.ok:
        return None
    entries = parse_feed(result.content)
    return entries[0] if entries else None


async def search_titles(client: PoliteClient, title: str, limit: int = 3) -> list[ArxivEntry]:
    clean = re.sub(r'["\\]', " ", title).strip()
    queries = [f'ti:"{clean}"', " AND ".join(f"all:{w}" for w in clean.split()[:12])]
    for query in queries:
        result = await client.get(
            API_URL,
            params={"search_query": query, "start": "0", "max_results": str(limit)},
            ttl=SEARCH_TTL,
        )
        if not result.ok:
            raise FetchError("The arXiv search didn't respond. Try again in a minute.")
        entries = parse_feed(result.content)
        if entries:
            return entries[:limit]
    return []
