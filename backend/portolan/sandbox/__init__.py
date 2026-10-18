"""Sandbox for LLM-written example and graph programs (spec §10.7.3). Never runs repository code."""

from __future__ import annotations

from portolan.sandbox.runner import run_program
from portolan.sandbox.types import SandboxResult

__all__ = ["SandboxResult", "run_program"]
