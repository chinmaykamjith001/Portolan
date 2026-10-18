"""Structured outputs for the example and graph prompts (spec Appendix D.5, §10.7.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ExampleOut(BaseModel):
    caption: str  # what the example computes, in plain words
    source_label: Literal["generic", "paper_notation"]
    program: str  # <= 40 lines of Python that prints one JSON table
    note: str | None = None  # one sentence on what to notice


class GraphOut(BaseModel):
    template: Literal["bar", "line", "heatmap", "before_after", "distribution", "none"]
    caption: str = ""
    program: str = ""  # prints one JSON object in the template's data shape
    note: str | None = None
