"""PDF → PaperDoc with heuristic structure (last resort; `low_structure=True`).

Headings are short blocks that are bigger or bolder than body text and look like
"3.2 Title" (or "A.1 Title" after the references), or are a known unnumbered heading.
Displayed equations are short lines ending in "(n)". Text after "References" goes to the
bibliography until an appendix heading appears.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pymupdf

from portolan.ingest.anchors import DocBuilder
from portolan.models.paper import PaperDoc, PaperRef

NUMBERED = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?P<title>[A-Z][^\n]{1,90})$")
LETTERED = re.compile(
    r"^(?:Appendix\s+)?(?P<num>[A-H](?:\.\d{1,2}){0,3})\.?\s+(?P<title>[A-Z][^\n]{1,90})$"
)
UNNUMBERED = {
    "abstract", "introduction", "related work", "background", "method", "methods", "experiments",
    "results", "discussion", "conclusion", "conclusions", "acknowledgments", "acknowledgements",
    "references", "bibliography", "appendix", "limitations",
}  # fmt: skip
EQUATION = re.compile(r"^(?P<body>.{1,200}?)\s*\((?P<num>\d{1,3}[a-z]?)\)$")
MATHY = re.compile(r"[=∑∏∫√≤≥≈∈×·^_]|\b(?:softmax|exp|log|max|min)\b")  # noqa: RUF001
NOISE = re.compile(r"^(?:\d{1,4}|arXiv:\S+.*|Preprint.*|Under review.*)$", re.I)
URL = re.compile(r"https?://[^\s)>\]]+")
FLOAT_CAPTION = re.compile(
    r"^(?P<kind>Table|Figure|Fig\.|Algorithm)\s+(?P<num>\d{1,3})\s*[:.]\s*(?P<text>.*)", re.S
)


@dataclass
class Block:
    text: str
    size: float
    bold: bool
    page: int
    lines: int


def _blocks(doc: Any) -> list[Block]:
    out: list[Block] = []
    for page_no, page in enumerate(doc):
        data: dict[str, Any] = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            lines: list[str] = []
            sizes: list[float] = []
            bold = True
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(str(s.get("text", "")) for s in spans).strip()
                if not text:
                    continue
                lines.append(text)
                for s in spans:
                    if str(s.get("text", "")).strip():
                        sizes.append(float(s.get("size", 0)))
                        is_bold = (
                            bool(int(s.get("flags", 0)) & 16)
                            or "bold" in str(s.get("font", "")).lower()
                        )
                        bold = bold and is_bold
            if lines:
                text = re.sub(r"-\n(?=[a-z])", "", "\n".join(lines))
                out.append(
                    Block(
                        text=text, size=max(sizes or [0]), bold=bold, page=page_no, lines=len(lines)
                    )
                )
    return out


def _body_size(blocks: list[Block]) -> float:
    weights: Counter[float] = Counter()
    for b in blocks:
        weights[round(b.size, 1)] += len(b.text)
    return weights.most_common(1)[0][0] if weights else 10.0


def parse_pdf(data: bytes, *, paper_id: str, ref: PaperRef) -> PaperDoc:
    doc: Any = pymupdf.open(stream=data, filetype="pdf")
    try:
        blocks = _blocks(doc)
        links: list[str] = []
        for page in doc:
            for link in page.get_links():
                uri = link.get("uri")
                if uri:
                    links.append(str(uri))
    finally:
        doc.close()

    body = _body_size(blocks)
    b = DocBuilder()
    for link in links:
        b.add_link(link)

    title = ""
    first_page = [blk for blk in blocks if blk.page == 0]
    if first_page:
        biggest = max(first_page, key=lambda blk: blk.size)
        if biggest.size > body + 1:
            title = " ".join(biggest.text.split("\n"))

    abstract_parts: list[str] = []
    references: list[str] = []
    mode = "front"  # front | abstract | body | references
    for blk in blocks:
        text = blk.text.strip()
        flat = re.sub(r"\s+", " ", text)
        if NOISE.match(flat) or flat == title:
            continue
        for url in URL.findall(flat):
            b.add_link(url)

        heading = None
        is_heading_style = (
            blk.lines <= 2 and len(flat) < 100 and (blk.size > body + 0.5 or blk.bold)
        )
        if is_heading_style:
            m = NUMBERED.match(flat)
            lm = LETTERED.match(flat) if (mode == "references" or b.in_appendix) else None
            if m:
                heading = (m.group("num"), m.group("title"))
            elif lm:
                heading = (lm.group("num"), lm.group("title"))
            elif flat.lower().rstrip(".:") in UNNUMBERED:
                heading = (None, flat.rstrip(".:"))

        if heading is not None:
            num, head = heading
            low = head.lower()
            if low == "abstract":
                mode = "abstract"
                continue
            if low in {"references", "bibliography"}:
                mode = "references"
                continue
            if low == "appendix" or (num and num[0].isalpha()):
                b.start_appendix()
                if low == "appendix" and not num:
                    mode = "body"
                    continue
            mode = "body"
            level = num.count(".") + 1 if num else 1
            b.section(level, num, head)
            continue

        if mode == "abstract":
            abstract_parts.append(flat)
        elif mode == "references":
            references.append(flat)
        elif mode == "body":
            eq = EQUATION.match(flat)
            fl = FLOAT_CAPTION.match(flat)
            if eq and len(flat) < 160 and MATHY.search(eq.group("body")):
                b.equation(eq.group("body"), eq.group("num"))
            elif fl:
                kind = {"Table": "table", "Algorithm": "algorithm"}.get(fl.group("kind"), "figure")
                b.float(kind, fl.group("text"), fl.group("num"))  # type: ignore[arg-type]
            else:
                b.paragraph(flat)
        elif mode == "front" and not title:
            title = flat

    return b.build(
        paper_id=paper_id,
        ref=ref,
        title=title,
        authors=[],
        abstract=" ".join(abstract_parts),
        references=references,
        low_structure=True,
    )
