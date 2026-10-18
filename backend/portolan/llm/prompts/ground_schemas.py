"""Structured output for the `ground_code` prompt (spec Appendix D.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SpanOut(BaseModel):
    path: str  # repo-relative, exactly as shown in the candidates
    start_line: int
    end_line: int


class NoteOut(BaseModel):
    line: int  # absolute line number in the file, inside one of the spans
    tag: str  # which part of the paper the line implements: "Eq. 1 · QKᵀ/√dₖ", "§3.2.3"
    note: str  # what the line does, at most 25 words
    kind: Literal["implements", "detail_not_in_paper", "mismatch", "context"]


class GroundOut(BaseModel):
    grounding: Literal["in_code", "in_library", "concept_only"]
    spans: list[SpanOut] = Field(default_factory=list[SpanOut])
    notes: list[NoteOut] = Field(default_factory=list[NoteOut])
    library_calls: list[str] = Field(default_factory=list[str])  # ["torch.softmax"]
    summary: str  # "Lines 38-45 implement Eq. 1."
