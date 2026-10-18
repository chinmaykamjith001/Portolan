"""Sandbox for LLM-written example and graph programs (spec §10.7.3). Never runs repository code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    output: object | None  # the parsed JSON the program printed last, if any
    stdout: str  # raw stdout (truncated)
    error: str | None  # plain-language reason when ok is False
    duration_s: float


async def run_program(source: str, *, timeout: float = 5.0, memory_mb: int = 256) -> SandboxResult:
    """Statically check, then run `source` in a locked-down subprocess (see §10.7.3)."""
    raise NotImplementedError("sandbox arrives with M6")
