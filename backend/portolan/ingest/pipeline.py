"""`PaperRef` → `PaperDoc` (spec §8 `ingest.fetch_and_parse`, §10.2).

arXiv papers: metadata from the arXiv API, then the first source that parses, in the order the
spec requires: arxiv.org/html → ar5iv → LaTeX e-print → PDF. DOI-only papers use their
open-access PDF. Uploads are parsed as PDF.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import posixpath
import tarfile
from dataclasses import dataclass, field

from portolan.ingest.arxiv import ArxivEntry, fetch_metadata
from portolan.ingest.http import FetchError, PoliteClient
from portolan.ingest.parse_html import NotLaTeXML, parse_html
from portolan.ingest.parse_latex import NoLatexSource, parse_latex
from portolan.ingest.parse_pdf import parse_pdf
from portolan.models.paper import PaperDoc, PaperRef
from portolan.store.db import Database
from portolan.store.papers import load_paper, save_paper

MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 5000
SOURCE_EXTENSIONS = (".tex", ".bbl", ".ltx")


class IngestError(Exception):
    """Plain-language ingestion failure with a next step for the user."""


@dataclass
class IngestReport:
    doc: PaperDoc
    attempts: list[str] = field(default_factory=list[str])  # e.g. ["arxiv_html: not available"]
    from_store: bool = False


def _safe_name(name: str) -> str | None:
    norm = posixpath.normpath(name.replace("\\", "/")).lstrip("/")
    if norm.startswith("..") or norm in {"", "."} or ":" in norm.split("/")[0]:
        return None
    return norm


def extract_source(data: bytes) -> dict[str, bytes] | None:
    """arXiv e-print bytes → {path: bytes} of .tex/.bbl files, entirely in memory.

    Returns None when the e-print is a PDF (no source available). Only regular files with safe
    relative names are read; sizes and member counts are capped.
    """
    if data[:4] == b"%PDF":
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            files: dict[str, bytes] = {}
            total = 0
            for i, member in enumerate(tar):
                if i >= MAX_MEMBERS:
                    break
                if not member.isreg() or member.size > MAX_MEMBER_BYTES:
                    continue
                name = _safe_name(member.name)
                if name is None or not name.lower().endswith(SOURCE_EXTENSIONS):
                    continue
                total += member.size
                if total > MAX_SOURCE_BYTES:
                    break
                fh = tar.extractfile(member)
                if fh is not None:
                    files[name] = fh.read(MAX_MEMBER_BYTES)
            return files
    except tarfile.TarError:
        pass
    # A single gzipped .tex file.
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    except (OSError, EOFError):
        return {}
    if raw[:4] == b"%PDF":
        return None
    return {"main.tex": raw[:MAX_SOURCE_BYTES]}


def _with_metadata(doc: PaperDoc, meta: ArxivEntry | None) -> PaperDoc:
    if meta is None:
        return doc
    return doc.model_copy(
        update={
            "title": meta.title or doc.title,
            "authors": meta.authors or doc.authors,
            "abstract": meta.abstract or doc.abstract,
            "ref": doc.ref.model_copy(update={"title": meta.title or doc.ref.title}),
        }
    )


async def fetch_and_parse(
    ref: PaperRef, client: PoliteClient, *, pdf_url: str | None = None
) -> IngestReport:
    if ref.arxiv_id:
        return await _arxiv(ref, client)
    if ref.doi:
        if not pdf_url:
            raise IngestError(
                "This DOI has no arXiv version and no open-access PDF Portolan can download. "
                "Upload the PDF instead."
            )
        result = await client.get(pdf_url)
        if not result.ok or result.content[:4] != b"%PDF":
            raise IngestError("Couldn't download the PDF for this DOI. Upload the PDF instead.")
        doc = await asyncio.to_thread(  # parsing is CPU-heavy: keep the event loop free
            parse_pdf,
            result.content,
            paper_id=doi_paper_id(ref.doi),
            ref=ref.model_copy(update={"source_kind": "pdf"}),
        )
        if ref.title:
            doc = doc.model_copy(update={"title": ref.title})
        return IngestReport(doc=doc, attempts=["pdf: ok"])
    raise IngestError("Nothing to ingest: no arXiv id or DOI.")


async def _arxiv(ref: PaperRef, client: PoliteClient) -> IngestReport:
    assert ref.arxiv_id is not None
    attempts: list[str] = []
    try:
        meta = await fetch_metadata(client, ref.arxiv_id, ref.version)
    except FetchError:
        meta = None
    if meta is None and ref.version is None:
        attempts.append("metadata: arXiv API unavailable; using the unversioned id")
    version = ref.version or (meta.version if meta else None)
    vid = f"{ref.arxiv_id}{version or ''}"
    ref = ref.model_copy(update={"version": version})
    paper_id = arxiv_paper_id(vid)

    # 1. LaTeXML HTML (arxiv.org/html, then ar5iv for older papers)
    for label, url in (
        ("arxiv_html", f"https://arxiv.org/html/{vid}"),
        ("ar5iv", f"https://ar5iv.labs.arxiv.org/html/{ref.arxiv_id}"),
    ):
        try:
            result = await client.get(url)
        except FetchError as exc:
            attempts.append(f"{label}: {exc}")
            continue
        if not result.ok or b"ltx_document" not in result.content:
            attempts.append(f"{label}: not available (HTTP {result.status})")
            continue
        try:
            doc = await asyncio.to_thread(parse_html, result.content, paper_id=paper_id, ref=ref)
        except NotLaTeXML:
            attempts.append(f"{label}: not a LaTeXML page")
            continue
        if doc.by_kind("section"):
            attempts.append(f"{label}: ok")
            return IngestReport(doc=_with_metadata(doc, meta), attempts=attempts)
        attempts.append(f"{label}: no sections found")

    # 2. LaTeX source
    pdf_bytes: bytes | None = None
    try:
        result = await client.get(f"https://arxiv.org/e-print/{vid}")
        if result.ok:
            files = extract_source(result.content)
            if files is None:
                attempts.append("latex: e-print is a PDF (no source)")
                pdf_bytes = result.content
            else:
                try:
                    doc = await asyncio.to_thread(parse_latex, files, paper_id=paper_id, ref=ref)
                except NoLatexSource:
                    attempts.append("latex: no main .tex file")
                else:
                    if doc.by_kind("section"):
                        attempts.append("latex: ok")
                        return IngestReport(doc=_with_metadata(doc, meta), attempts=attempts)
                    attempts.append("latex: no sections found")
        else:
            attempts.append(f"latex: not available (HTTP {result.status})")
    except FetchError as exc:
        attempts.append(f"latex: {exc}")

    # 3. PDF
    if pdf_bytes is None:
        try:
            result = await client.get(f"https://arxiv.org/pdf/{vid}")
        except FetchError as exc:
            attempts.append(f"pdf: {exc}")
            result = None
        if result is not None and result.ok and result.content[:4] == b"%PDF":
            pdf_bytes = result.content
        elif result is not None:
            attempts.append(f"pdf: not available (HTTP {result.status})")
    if pdf_bytes is not None:
        doc = await asyncio.to_thread(
            parse_pdf,
            pdf_bytes,
            paper_id=paper_id,
            ref=ref.model_copy(update={"source_kind": "pdf"}),
        )
        attempts.append("pdf: ok")
        return IngestReport(doc=_with_metadata(doc, meta), attempts=attempts)

    raise IngestError(
        "Couldn't download the paper source from arXiv. Try again, or upload the PDF."
    )


def arxiv_paper_id(versioned_id: str) -> str:
    """ "arxiv:2106.09685v2". Old-style ids ("hep-th/9711200") keep no "/": ids go in URL paths."""
    return f"arxiv:{versioned_id.replace('/', '_')}"


def doi_paper_id(doi: str) -> str:
    return f"doi:{doi.lower().replace('/', '_')}"


def upload_paper_id(data: bytes) -> str:
    """Uploaded PDFs are identified by content, so the same file maps to the same paper."""
    return f"upload:{hashlib.sha256(data).hexdigest()[:16]}"


def parse_upload(data: bytes, filename: str = "upload.pdf") -> PaperDoc:
    if data[:4] != b"%PDF":
        raise IngestError(f"{filename} doesn't look like a PDF. Upload a PDF file.")
    return parse_pdf(
        data,
        paper_id=upload_paper_id(data),
        ref=PaperRef(source_kind="upload", title=filename),
    )


async def ingest_ref(
    ref: PaperRef,
    client: PoliteClient,
    db: Database | None,
    *,
    pdf_url: str | None = None,
    refresh: bool = False,
    save: bool = True,
) -> IngestReport:
    """fetch_and_parse with the parsed-paper cache (keyed by versioned paper id)."""
    if db is not None and not refresh and ref.arxiv_id and ref.version:
        cached = load_paper(db, arxiv_paper_id(f"{ref.arxiv_id}{ref.version}"))
        if cached is not None:
            return IngestReport(doc=cached, attempts=["stored: ok"], from_store=True)
    report = await fetch_and_parse(ref, client, pdf_url=pdf_url)
    if db is not None and save:
        save_paper(db, report.doc)
    return report
