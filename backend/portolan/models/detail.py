"""Node details (spec §9.4). M5 ships explanations and edge explanations; the rest comes in M6."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from portolan.llm.prompts.detail_schemas import ExplainSections

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
