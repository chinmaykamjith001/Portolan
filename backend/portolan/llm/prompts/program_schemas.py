"""Structured outputs for the example and graph prompts (spec Appendix D.5, §10.7.4)."""

from __future__ import annotations

from typing import Annotated, Literal

from portolan.llm.prompts.lenient import ReplySchema, lenient

SourceLabelOut = Literal["generic", "paper_notation"]
TemplateOut = Literal["bar", "line", "heatmap", "before_after", "distribution", "none"]


class ExampleOut(ReplySchema):
    caption: str  # what the example computes, in plain words
    source_label: Annotated[SourceLabelOut, lenient(SourceLabelOut, "generic")] = "generic"
    program: str  # <= 40 lines of Python that prints one JSON table
    note: str | None = None  # one sentence on what to notice


class GraphOut(ReplySchema):
    template: Annotated[TemplateOut, lenient(TemplateOut, "none")]
    caption: str = ""
    program: str = ""  # prints one JSON object in the template's data shape
    note: str | None = None
