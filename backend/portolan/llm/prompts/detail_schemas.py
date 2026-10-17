"""Structured outputs for detail prompts (spec Appendix D.3, D.6)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExplainSections(BaseModel):
    what_it_is: str  # intuition first, plain words
    how_it_works: str  # step by step; the paper's notation in $...$ where it helps
    why_its_here: str  # its role in this paper and under the parent the reader came from
    where_it_appears: list[str] = Field(default_factory=list[str])  # anchor ids from the input
    common_confusion: str  # one misconception and its correction


class ExplainOut(BaseModel):
    gist: str  # one sentence
    sections: ExplainSections


class EdgeOut(BaseModel):
    headline: str  # the relationship restated as a sentence
    body: str  # 80-150 words on why the relationship holds
    why_it_matters: str  # one sentence
