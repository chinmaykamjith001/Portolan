"""Structured output for the `ground_code` prompt (spec Appendix D.4)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from portolan.llm.prompts.lenient import ReplySchema, lenient

NoteKindOut = Literal["implements", "detail_not_in_paper", "mismatch", "context"]
GroundingOut = Literal["in_code", "in_library", "concept_only"]


class SpanOut(ReplySchema):
    path: str  # repo-relative, exactly as shown in the candidates
    start_line: int
    end_line: int


class NoteOut(ReplySchema):
    line: int  # absolute line number in the file, inside one of the spans
    tag: str  # which part of the paper the line implements: "Eq. 1 · QKᵀ/√dₖ", "§3.2.3"
    note: str  # what the line does, at most 25 words
    kind: Annotated[NoteKindOut, lenient(NoteKindOut, "context")]


class GroundOut(ReplySchema):
    grounding: Annotated[GroundingOut, lenient(GroundingOut, "concept_only")] = "concept_only"
    spans: list[SpanOut] = Field(default_factory=list[SpanOut])
    notes: list[NoteOut] = Field(default_factory=list[NoteOut])
    library_calls: list[str] = Field(default_factory=list[str])  # ["torch.softmax"]
    summary: str  # "Lines 38-45 implement Eq. 1."
