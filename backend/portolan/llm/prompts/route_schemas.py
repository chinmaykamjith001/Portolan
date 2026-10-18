"""Structured output for `route_section` (spec §10.6.2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RouteStopOut(BaseModel):
    key: str  # echoes the input stop key ("s1", "s2", ...)
    title: str  # short: what the paper does here, not just the heading
    role: str = "other"  # first_mention | definition | setting | result | argument | other
    summary: str = ""  # paraphrase of the passage, at most 40 words
    concept_id: str | None = None  # an id from the candidate list, or null
    transition_to_next: str | None = None  # the connective that leads to the next stop


class RouteSectionOut(BaseModel):
    stops: list[RouteStopOut] = Field(default_factory=list[RouteStopOut])
