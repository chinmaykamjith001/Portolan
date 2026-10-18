"""Node details (spec §9.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from portolan.llm.prompts.detail_schemas import ExplainSections
from portolan.models.code import CodeSpan, LineNote
from portolan.models.concept import Grounding

Angle = Literal["default", "simpler", "mathematical", "analogy", "why", "another_take"]


class Explanation(BaseModel):
    concept_id: str
    angle: Angle
    path_context: list[str] = Field(default_factory=list[str])  # concept ids master → here
    gist: str
    sections: ExplainSections  # structured form the drawer renders (DECISIONS.md)
    body_markdown: str  # the same content as Markdown with small headings
    anchors_cited: list[str] = Field(default_factory=list[str])
    model: str
    created_at: datetime
    word_count: int = 0


class EdgeExplanation(BaseModel):
    link_id: str
    headline: str  # "Self-attention needs positional encoding"
    body_markdown: str  # 80-150 words
    why_it_matters: str  # one sentence


class CodeGrounding(BaseModel):
    concept_id: str
    grounding: Grounding
    spans: list[CodeSpan] = Field(default_factory=list[CodeSpan])
    notes: list[LineNote] = Field(default_factory=list[LineNote])
    library_calls: list[str] = Field(default_factory=list[str])  # ["torch.softmax"]
    summary: str = ""  # "Lines 41-45 implement Eq. 1."
    # Filled by the grounder so the drawer needs no second request: the file text of each span,
    # {path: {line_number: text}}, and a link to the file at the commit.
    code: dict[str, dict[int, str]] = Field(default_factory=dict[str, dict[int, str]])
    links: dict[str, str] = Field(default_factory=dict[str, str])  # path -> URL at the commit


class Example(BaseModel):
    concept_id: str
    variant: int = 0
    source_label: Literal["generic", "paper_notation", "from_repo_run"]
    caption: str
    program: str  # the snippet that was executed
    columns: list[str]
    rows: list[list[str]]  # executed output, never LLM-written numbers
    note: str | None = None


GraphTemplate = Literal["bar", "line", "heatmap", "before_after", "distribution"]


class GraphSpec(BaseModel):
    concept_id: str
    template: GraphTemplate
    caption: str
    data: dict[str, object]  # computed by the sandbox from a program, never typed by the LLM
    note: str | None = None
