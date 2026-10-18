"""Route (spec §9.5): the paper as a road, in the paper's own order."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StopRole = Literal["first_mention", "definition", "setting", "result", "argument", "other"]


class Stop(BaseModel):
    id: str
    route_id: str
    order: int
    title: str  # short, e.g. "Scaled dot-product attention"
    section_label: str  # "§3.2.1" or "§3.2.1 ¶2"
    anchor_id: str
    role: StopRole = "other"
    concept_id: str | None = None
    summary: str = ""  # paraphrase of what the paper says here (≤ 40 words)
    transition_to_next: str | None = None  # "so the authors drop recurrence,"
    sub_route_id: str | None = None  # set when jumping in is possible
    segment_title: str | None = (
        None  # first stop of each top-level section: "§3 Model architecture"
    )
    # Recurring concepts (§10.6.4): stop ids where the same concept appears.
    first_stop_id: str | None = None  # set on later mentions: where it was first mentioned
    seen_again_at: list[str] = Field(default_factory=list[str])  # set on the first mention
    # Appendix side road (§10.6.1): appendix section anchor ids this stop refers to.
    side_roads: list[str] = Field(default_factory=list[str])


class Route(BaseModel):
    id: str  # "main" or "<stop_id>"
    paper_id: str
    parent_stop_id: str | None = None
    stops: list[Stop] = Field(default_factory=list[Stop])
