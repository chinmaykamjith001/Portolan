"""Findings and FAQ items (spec §9.6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from portolan.models.code import CodeSpan

ClaimType = Literal[
    "hyperparameter",
    "architecture",
    "algorithm_step",
    "objective",
    "data_processing",
    "evaluation",
    "other",
]
Verdict = Literal["matches", "mismatch", "undocumented_in_paper", "uncertain", "not_found"]
Severity = Literal["high", "medium", "low"]
Method = Literal["literal_match", "static", "llm_judge", "executed_test"]


class Finding(BaseModel):
    id: str
    claim_text: str  # paraphrased claim
    claim_type: ClaimType
    paper_anchor: str
    code_spans: list[CodeSpan] = Field(default_factory=list[CodeSpan])
    verdict: Verdict
    severity: Severity
    confidence: float  # 0-1
    method: Method
    evidence: str  # paper side vs code side, in plain words
    concept_ids: list[str] = Field(default_factory=list[str])


SourceKind = Literal[
    "reviewer_discussion",
    "github_issue",
    "paper_page_discussion",
    "finding",
    "anticipated",
    "user_asked",
]


class FAQItem(BaseModel):
    id: str
    question: str
    answer_markdown: str
    source_kind: SourceKind
    source_url: str | None = None
    source_label: str = ""  # "From reviewer discussion", "From GitHub issue #123", ...
    concept_ids: list[str] = Field(default_factory=list[str])
    anchors_cited: list[str] = Field(default_factory=list[str])
    supported: bool = True  # False: "not supported by the sources" (§10.10 Ask box)
