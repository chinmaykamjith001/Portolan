"""Run an engine over sampled records: resumable, one failing record never stops the rest."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import portolan
from portolan.evalharness.checkpoint import Checkpoint, RecordResult
from portolan.evalharness.records import EvalRecord
from portolan.llm.ratelimit import DailyBudgetExhausted
from portolan.models.finding import Finding


class BudgetExhaustedStop(Exception):
    """Raised (or DailyBudgetExhausted let through) when today's requests are gone."""


class EvalError(Exception):
    """A plain-language reason one record couldn't be evaluated (no traceback shown to the user)."""


@dataclass
class EngineOutput:
    findings: list[Finding]
    requests_used: int = 0
    note: str | None = None


class Engine(Protocol):
    version: str

    async def check(self, record: EvalRecord, /) -> EngineOutput: ...


@dataclass
class RunSummary:
    ran: int = 0
    failed: int = 0
    already_done: int = 0
    pending: int = 0  # not run because the budget ran out
    stopped_for_budget: bool = False
    errors: list[str] = field(default_factory=list[str])


def engine_fingerprint() -> str:
    """Changes whenever the verification engine or its prompts change, so old results are never
    mistaken for new ones. `ENGINE_VERSION` in verify/engine.py, if defined, is included."""
    root = Path(__file__).resolve().parents[1]
    parts: list[bytes] = [portolan.__version__.encode()]
    files = sorted((root / "verify").glob("*.py"))
    files += sorted((root / "llm" / "prompts").glob("claim*.py"))
    files += sorted((root / "llm" / "prompts").glob("verify*.py"))
    files += sorted((root / "llm" / "prompts").glob("judge*.py"))
    for f in files:
        parts.append(f.name.encode() + f.read_bytes())
    return hashlib.sha1(b"\0".join(parts)).hexdigest()[:10]


async def run(
    records: list[EvalRecord],
    engine: Engine,
    checkpoint: Checkpoint,
    *,
    retry_failed: bool = True,
    progress: Callable[[str], None] = lambda _msg: None,
) -> RunSummary:
    existing = checkpoint.load()
    summary = RunSummary()
    for i, rec in enumerate(records, start=1):
        prior = existing.get((rec.id, engine.version))
        if prior is not None and (prior.status == "done" or not retry_failed):
            summary.already_done += 1
            continue
        progress(f"[{i}/{len(records)}] {rec.id}: {rec.paper}")
        try:
            out = await engine.check(rec)
        except (BudgetExhaustedStop, DailyBudgetExhausted):
            summary.stopped_for_budget = True
            summary.pending = sum(
                1
                for r in records[i - 1 :]
                if (p := existing.get((r.id, engine.version))) is None or p.status != "done"
            )
            progress(
                "Today's request budget is used up. Progress is saved; "
                "run the same command tomorrow."
            )
            return summary
        except Exception as exc:  # isolate: one bad paper or repo must not cost the whole run
            message = (
                str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
            )
            checkpoint.append(
                RecordResult(
                    record_id=rec.id, engine_version=engine.version, status="failed", error=message
                )
            )
            summary.failed += 1
            summary.errors.append(f"{rec.id}: {message}")
            progress(f"  failed: {message}")
            continue
        checkpoint.append(
            RecordResult(
                record_id=rec.id,
                engine_version=engine.version,
                status="done",
                findings=out.findings,
                requests_used=out.requests_used,
                note=out.note,
            )
        )
        summary.ran += 1
        progress(f"  done: {len(out.findings)} finding(s), {out.requests_used} request(s)")
    return summary


def results_for(
    records: list[EvalRecord], checkpoint: Checkpoint, engine_version: str
) -> dict[str, RecordResult]:
    """Latest result per sampled record for this engine version."""
    loaded = checkpoint.load()
    return {
        r.id: loaded[(r.id, engine_version)] for r in records if (r.id, engine_version) in loaded
    }
