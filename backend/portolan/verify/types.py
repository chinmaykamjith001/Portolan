"""Shapes shared by the verification steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from portolan.llm.prompts.verify_schemas import ClaimTypeOut
from portolan.models.code import CodeSpan, Symbol
from portolan.models.finding import Method, Severity, Verdict


class Claim(BaseModel):
    key: str
    anchor: str
    claim_type: ClaimTypeOut
    subject: str
    value: str | None = None
    text: str


@dataclass
class ValueItem:
    """One named value in the code: a constant, config key, CLI default or parameter default."""

    name: str
    value: str
    symbol: Symbol
    line: int
    source: Literal["constant", "config", "cli", "default"]


@dataclass
class Decision:
    """What a rung of the ladder decided about one claim."""

    verdict: Verdict
    confidence: float
    method: Method
    code_side: str  # what the code does, naming file and line
    spans: list[CodeSpan] = field(default_factory=list[CodeSpan])
    severity: Severity | None = None  # None: derive from the claim type
    paper_side: str | None = None  # the judge's wording of the paper's side, if it gave one
