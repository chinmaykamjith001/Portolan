"""Claim extraction (spec §10.9.1): checkable statements from the method and experiment text."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from portolan.llm.gateway import Gateway
from portolan.llm.prompts import claims_extract
from portolan.llm.prompts.verify_schemas import ClaimsOut
from portolan.models.paper import Anchor, PaperDoc
from portolan.verify.types import Claim

MAX_CLAIMS = 40
MAX_CALLS = 6
BATCH_CHARS = 12_000
PASSAGE_CHARS = 1400
MIN_PASSAGE_CHARS = 40
SYMBOLS_SENT = 30

INCLUDE = re.compile(
    r"method|model|architecture|approach|training|experiment|evaluation|setup|implementation|"
    r"optimi[sz]|regulari[sz]|hyperparameter|detail|algorithm|network|framework|proposed",
    re.I,
)
EXCLUDE = re.compile(
    r"introduction|related|background|conclusion|discussion|acknowledg|reference|abstract|"
    r"limitation|broader|ethic",
    re.I,
)
APPENDIX_OK = re.compile(
    r"hyperparameter|implementation|training detail|experimental (setup|detail)|"
    r"architecture detail",
    re.I,
)
_PASSAGE_KINDS = ("paragraph", "table", "algorithm")


def claim_key(anchor: str, subject: str, value: str | None) -> str:
    raw = f"{anchor}|{subject.strip().lower()}|{value or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def passages(doc: PaperDoc) -> list[dict[str, str]]:
    """Paragraphs and tables from the sections that describe how the method is built and run."""
    current_top: Anchor | None = None
    current: Anchor | None = None
    entries: list[tuple[Anchor, Anchor, Anchor | None]] = []
    for a in sorted(doc.anchors, key=lambda x: x.order):
        if a.kind == "section":
            if (a.level or 1) <= 1:
                current_top = a
            current = a
        elif a.kind in _PASSAGE_KINDS and current_top is not None:
            entries.append((a, current_top, current))
    main = [t for _, t, _ in entries if not t.is_appendix]
    method_like = any(INCLUDE.search(t.text) and not EXCLUDE.search(t.text) for t in main)

    out: list[dict[str, str]] = []
    for a, top, near in entries:
        if len(a.text.strip()) < MIN_PASSAGE_CHARS:
            continue
        titles = f"{top.text} {near.text if near else ''}"
        if top.is_appendix:
            keep = bool(APPENDIX_OK.search(titles))
        elif method_like:
            keep = bool(INCLUDE.search(titles)) and not EXCLUDE.search(top.text)
        else:
            keep = not EXCLUDE.search(top.text)
        if keep:
            out.append({"id": a.id, "label": a.label, "text": a.text.strip()[:PASSAGE_CHARS]})
    return out


def batches(items: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    out: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    size = 0
    for p in items:
        if current and size + len(p["text"]) > BATCH_CHARS:
            out.append(current)
            current, size = [], 0
        current.append(p)
        size += len(p["text"])
    if current:
        out.append(current)
    return out


async def extract_claims(doc: PaperDoc, gateway: Gateway) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[str] = set()
    symbols = dict(list(doc.symbols.items())[:SYMBOLS_SENT])
    for batch in batches(passages(doc))[:MAX_CALLS]:
        inputs: dict[str, Any] = {"paper_title": doc.title, "passages": batch, "symbols": symbols}
        result = await gateway.call(
            "strong", claims_extract.TEMPLATE, inputs, ClaimsOut, "P2", doc.paper_id
        )
        ids = {p["id"] for p in batch}
        for c in result.parsed.claims:  # type: ignore[attr-defined]
            if c.anchor not in ids or not c.subject.strip() or not c.text.strip():
                continue  # an anchor the model made up is not a claim we can show
            key = claim_key(c.anchor, c.subject, c.value)
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                Claim(
                    key=key,
                    anchor=c.anchor,
                    claim_type=c.claim_type,
                    subject=c.subject.strip(),
                    value=c.value.strip() if c.value and c.value.strip() else None,
                    text=c.text.strip(),
                )
            )
            if len(claims) >= MAX_CLAIMS:
                return claims
    return claims
