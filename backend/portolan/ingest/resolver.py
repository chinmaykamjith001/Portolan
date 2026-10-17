"""Turn raw user input into a `PaperRef` (spec §10.1).

Accepts arXiv abs/pdf/html URLs, ar5iv, alphaxiv and huggingface.co/papers URLs, bare arXiv ids
(new and old style, with or without version, optional "arXiv:" prefix), DOIs (bare or doi.org
URLs) and free-text titles. Titles never resolve silently: the caller gets up to 3 candidates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote

from portolan.ingest.arxiv import ArxivEntry, search_titles
from portolan.ingest.http import PoliteClient
from portolan.models.paper import PaperRef

NEW_ID = r"\d{4}\.\d{4,5}"
OLD_ID = r"[a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7}"
ARXIV_ID = rf"(?P<id>{NEW_ID}|{OLD_ID})(?P<version>v\d+)?"

_URL_PATTERNS = [
    re.compile(
        rf"(?:export\.)?arxiv\.org/(?:abs|pdf|html|e-print|format)/{ARXIV_ID}(?:\.pdf)?(?:[/?#].*)?$",
        re.I,
    ),
    re.compile(rf"ar5iv(?:\.labs)?\.(?:arxiv\.)?org/(?:html|abs)/{ARXIV_ID}(?:[/?#].*)?$", re.I),
    re.compile(rf"alphaxiv\.org/(?:abs|overview|pdf)/{ARXIV_ID}(?:[/?#].*)?$", re.I),
    re.compile(rf"huggingface\.co/papers/{ARXIV_ID}(?:[/?#].*)?$", re.I),
]
_BARE = re.compile(rf"^(?:arxiv:\s*)?{ARXIV_ID}$", re.I)
_DOI = re.compile(r"(?:doi\.org/|doi:\s*|^)(?P<doi>10\.\d{4,9}/[^\s\"<>]+)$", re.I)
_ARXIV_DOI = re.compile(rf"^10\.48550/arxiv\.{ARXIV_ID}$", re.I)

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
S2_FIELDS = "externalIds,title,openAccessPdf"


@dataclass
class TitleCandidate:
    arxiv_id: str
    version: str | None
    title: str
    authors: list[str]
    year: str


@dataclass
class ResolveResult:
    ref: PaperRef | None = None
    candidates: list[TitleCandidate] = field(default_factory=list[TitleCandidate])
    error: str | None = None
    pdf_url: str | None = None  # open-access PDF for DOI-only papers

    @property
    def needs_confirmation(self) -> bool:
        return self.ref is None and bool(self.candidates)


def parse_arxiv(raw: str) -> tuple[str, str | None] | None:
    s = unquote(raw.strip())
    m = _BARE.match(s)
    if m:
        return m.group("id"), m.group("version")
    for pattern in _URL_PATTERNS:
        m = pattern.search(s)
        if m:
            return m.group("id"), m.group("version")
    return None


def parse_doi(raw: str) -> str | None:
    m = _DOI.search(unquote(raw.strip()))
    return m.group("doi").rstrip(".") if m else None


def _candidate(e: ArxivEntry) -> TitleCandidate:
    return TitleCandidate(
        arxiv_id=e.arxiv_id,
        version=e.version,
        title=e.title,
        authors=e.authors,
        year=e.published[:4],
    )


async def resolve(raw: str, client: PoliteClient) -> ResolveResult:
    text = raw.strip()
    if not text:
        return ResolveResult(error="Paste an arXiv link, DOI, PDF or title.")

    arxiv = parse_arxiv(text)
    if arxiv:
        return ResolveResult(ref=PaperRef(arxiv_id=arxiv[0], version=arxiv[1]))

    doi = parse_doi(text)
    if doi:
        m = _ARXIV_DOI.match(doi)
        if m:
            return ResolveResult(ref=PaperRef(arxiv_id=m.group("id"), version=m.group("version")))
        return await _resolve_doi(doi, client)

    if re.match(r"^https?://", text):
        return ResolveResult(
            error="That link isn't an arXiv, DOI or paper-page link Portolan recognizes. "
            "Try the arXiv link, the DOI, or upload the PDF."
        )

    entries = await search_titles(client, text)
    if not entries:
        return ResolveResult(error="No arXiv paper matches that title. Try the arXiv link or DOI.")
    return ResolveResult(candidates=[_candidate(e) for e in entries])


async def _resolve_doi(doi: str, client: PoliteClient) -> ResolveResult:
    result = await client.get(
        S2_URL.format(doi=quote(doi, safe="/")), params={"fields": S2_FIELDS}, ttl=7 * 24 * 3600.0
    )
    if result.status == 404:
        return ResolveResult(error=f"Semantic Scholar doesn't know the DOI {doi}. Upload the PDF.")
    if not result.ok:
        return ResolveResult(
            error="Couldn't look up that DOI right now. Try again, or upload the PDF."
        )
    body: dict[str, Any] = json.loads(result.content)
    external: dict[str, Any] = body.get("externalIds") or {}
    title = body.get("title")
    arxiv_id = external.get("ArXiv")
    if arxiv_id:
        parsed = parse_arxiv(str(arxiv_id))
        if parsed:
            return ResolveResult(
                ref=PaperRef(arxiv_id=parsed[0], version=parsed[1], doi=doi, title=title)
            )
    pdf: dict[str, Any] = body.get("openAccessPdf") or {}
    return ResolveResult(
        ref=PaperRef(doi=doi, title=title, source_kind="pdf"), pdf_url=pdf.get("url") or None
    )
