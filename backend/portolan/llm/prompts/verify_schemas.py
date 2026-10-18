"""Structured outputs for the verification prompts (spec §10.9, Appendix D.7)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from portolan.llm.prompts.lenient import ReplySchema, lenient, lenient_float

VerdictLit = Literal["matches", "mismatch", "uncertain", "not_found"]
SeverityLit = Literal["low", "medium"]

ClaimTypeOut = Literal[
    "hyperparameter",
    "architecture",
    "algorithm_step",
    "objective",
    "data_processing",
    "evaluation",
    "other",
]


class ClaimOut(ReplySchema):
    anchor: str  # id of the passage the claim comes from, exactly as given
    claim_type: Annotated[ClaimTypeOut, lenient(ClaimTypeOut, "other")] = "other"
    subject: str  # what is being claimed about: "dropout rate", "number of encoder layers"
    value: str | None = (
        None  # the value as the paper writes it: "0.1", "6", "10%"; None if not numeric
    )
    text: str  # one short paraphrase of the whole claim (at most 30 words)


class ClaimsOut(ReplySchema):
    claims: list[ClaimOut] = Field(default_factory=list[ClaimOut])


class VerdictOut(ReplySchema):
    key: str  # the claim key from the input
    verdict: Annotated[VerdictLit, lenient(VerdictLit, "uncertain")]
    confidence: Annotated[float, lenient_float(0.5)]  # 0 to 1
    paper_side: str  # what the paper says, in a few words
    code_side: str  # what the code does, in a few words, naming the file
    path: str | None = None  # the file the evidence is in, exactly as shown
    start_line: int | None = None
    end_line: int | None = None


class JudgeOut(ReplySchema):
    verdicts: list[VerdictOut] = Field(default_factory=list[VerdictOut])


class UndocumentedOut(ReplySchema):
    title: str  # "Gradient clipping at 1.0"
    evidence: str  # what the code does and why the paper's description doesn't cover it
    path: str
    start_line: int
    end_line: int
    severity: Annotated[SeverityLit, lenient(SeverityLit, "low")] = "low"
    confidence: Annotated[float, lenient_float(0.5)]  # 0 to 1


class ReverseOut(ReplySchema):
    items: list[UndocumentedOut] = Field(default_factory=list[UndocumentedOut])
