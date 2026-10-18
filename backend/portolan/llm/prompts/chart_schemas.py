"""Structured outputs for Chart prompts (spec Appendix D.1, D.2). Authoritative over the appendix.

These models are what the gateway validates LLM replies against; one schema-repair retry
happens on failure (§11.1). Keep them flat and simple: Gemini structured output handles
plain objects, arrays, enums and strings best.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from portolan.llm.prompts.lenient import lenient
from portolan.models.concept import LinkKind

GroundingGuess = Literal["in_code", "in_library", "concept_only", "unknown"]


class CenterOut(BaseModel):
    title: str
    gist: str
    anchors: list[str] = Field(default_factory=list[str])


class ConceptOut(BaseModel):
    key: str  # short kebab-case key, unique within the reply ("self-attention")
    title: str
    gist: str  # one sentence, ≤ 25 words
    anchors: list[str] = Field(default_factory=list[str])  # ids from the provided anchor list
    is_first_principle: bool = False
    grounding_guess: Annotated[GroundingGuess, lenient(GroundingGuess, "unknown")] = "unknown"


class LinkOut(BaseModel):
    source: str  # a concept key, or "center"
    target: str  # a concept key, or "center"
    label: str  # verb phrase: source — label → target reads as a sentence
    kind: Annotated[LinkKind, lenient(LinkKind, "other")] = "other"


class MasterLayerOut(BaseModel):
    center: CenterOut
    concepts: list[ConceptOut]  # target 10-20 (prompt); the builder trims extras
    links: list[LinkOut]


class SkeletonLayerOut(BaseModel):
    center_key: str  # the key of the concept being decomposed, exactly as given in the input
    concepts: list[ConceptOut]  # target 4-10 (prompt); the builder trims extras
    links: list[LinkOut]  # among the sub-concepts and from "center"


class SkeletonBatchOut(BaseModel):
    layers: list[SkeletonLayerOut]


class SameConceptOut(BaseModel):
    same: bool
    reason: str  # one short sentence
