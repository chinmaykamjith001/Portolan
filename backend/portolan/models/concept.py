"""Concept graph (spec §9.3). Concepts form a DAG: one Concept can appear in several Layers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Grounding = Literal["in_code", "in_library", "shown_by_running", "concept_only", "unknown"]
GenState = Literal["skeleton", "detailed", "generating", "failed"]
LinkKind = Literal["part_of", "uses", "causes", "enables", "contrasts", "example_of", "other"]

MASTER = "master"  # Layer.id of the top layer


class Concept(BaseModel):
    id: str  # paper-scoped slug, e.g. "scaled-dot-product-attention"
    canonical_name: str  # normalized name for dedupe ("softmax")
    title: str  # display name ("Scaled dot-product attention")
    gist: str  # one sentence, ≤ 25 words
    grounding: Grounding = "unknown"
    is_first_principle: bool = False  # leaf: do not decompose further
    depth: int  # shortest depth from the master layer (master concepts are 1, the center is 0)
    anchors: list[str] = Field(default_factory=list[str])  # paper anchor ids
    has_layer: bool = False  # True if it can be jumped into (its child layer exists)
    gen_state: GenState = "skeleton"
    issue_count_self: int = 0  # findings attached directly
    # future: global_concept_id for the cross-paper library


class Link(BaseModel):
    id: str  # "<layer_id>:<source>-><target>"
    layer_id: str
    source: str  # concept id
    target: str  # concept id
    label: str  # short verb phrase, reads as a sentence: "is computed by"
    kind: LinkKind = "other"


class Layer(BaseModel):
    id: str  # = center concept id, or "master"
    paper_id: str
    center: str  # concept id
    concepts: list[str]  # excluding center
    links: list[Link]
    gen_state: GenState = "skeleton"
