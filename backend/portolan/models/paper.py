"""Papers and anchors (spec §9.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["arxiv_html", "arxiv_latex", "pdf", "upload"]
AnchorKind = Literal["section", "paragraph", "equation", "table", "figure", "algorithm"]


class PaperRef(BaseModel):
    arxiv_id: str | None = None  # "1706.03762"
    version: str | None = None  # "v7"
    doi: str | None = None
    title: str | None = None
    source_kind: SourceKind = "arxiv_html"


class Anchor(BaseModel):
    id: str  # stable, e.g. "sec:3.2.1", "para:3.2.1:2", "eq:1", "tab:3"
    kind: AnchorKind
    label: str  # human label: "§3.2.1", "Eq. 1", "Table 3"
    text: str  # raw text (LaTeX kept for equations)
    parent_id: str | None = None
    order: int  # document order
    level: int | None = None  # sections only: 1 = section, 2 = subsection, ... (DECISIONS.md)
    is_appendix: bool = False  # DECISIONS.md: needed for Route side roads


class PaperDoc(BaseModel):
    paper_id: str  # internal id, e.g. "arxiv:1706.03762v7"
    ref: PaperRef
    title: str
    authors: list[str]
    abstract: str
    anchors: list[Anchor]
    equation_refs: dict[str, list[str]] = Field(default_factory=dict)  # eq id → referencing anchors
    symbols: dict[str, str] = Field(default_factory=dict)  # "d_k": "dimension of the keys"
    links_found: list[str] = Field(default_factory=list)  # URLs in the paper (for the repo finder)
    references: list[str] = Field(default_factory=list)  # bibliography entries (DECISIONS.md)
    low_structure: bool = False  # parsed from PDF; structure may be approximate

    def by_kind(self, kind: AnchorKind) -> list[Anchor]:
        return [a for a in self.anchors if a.kind == kind]

    def anchor(self, anchor_id: str) -> Anchor | None:
        return next((a for a in self.anchors if a.id == anchor_id), None)

    def children(self, anchor_id: str | None) -> list[Anchor]:
        return [a for a in self.anchors if a.parent_id == anchor_id]
