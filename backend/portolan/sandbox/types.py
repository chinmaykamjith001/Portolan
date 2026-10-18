"""Result type shared by the sandbox modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    output: object | None  # the parsed JSON the program printed last, if any
    stdout: str  # raw stdout (truncated)
    error: str | None  # plain-language reason when ok is False
    duration_s: float
