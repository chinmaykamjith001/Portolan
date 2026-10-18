"""Turning a failed background job into a visible error instead of an endless "generating"."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi.responses import JSONResponse

from portolan.api.services import Services

MAX_COOLDOWN_SECONDS = 600
BASE_COOLDOWN_SECONDS = 10


# Errors whose message is already written for the reader ("Gemini rejected the API key. ...").
OWN_WORDS = {
    "ProviderError", "ProviderTransient", "ProviderQuotaExhausted", "DailyBudgetExhausted",
    "DetailError", "LLMValidationError", "IngestError", "FetchError", "UploadError",
}  # fmt: skip


def failure_message(error: str | None) -> str:
    """Plain-language reason for a failed job (the raw error is only a technical fallback)."""
    text = (error or "").strip()
    kind, _, message = text.partition(": ")
    if kind in OWN_WORDS and message:
        return message
    if "No recorded response" in text:
        return (
            "Portolan is in mock mode and has no recorded answer for this step. "
            "Set PORTOLAN_LLM_PROVIDER=gemini in .env, then restart the backend."
        )
    if "RateLimitExhausted" in text:
        return "Gave up waiting for the free-tier rate limit. Try again in a few minutes."
    if not text:
        return "This step failed. Try again in a moment."
    return f"This step failed ({text[:240]}). Try again in a moment."


def _age_seconds(updated_at: str) -> float:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(updated_at)).total_seconds()
    except ValueError:
        return float("inf")


def recent_failure(svc: Services, key: str) -> str | None:
    """The reason, if this job failed recently enough that it should not simply run again.

    A failed job is retried only after a cooldown (10 s, doubling per attempt, at most 10 min),
    so a page that keeps asking cannot loop on a deterministic failure or burn free-tier requests.
    """
    live = svc.queue.get(key)
    if live is not None and live.state in ("queued", "running"):
        return None
    row = svc.db.fetchone(
        "SELECT state, error, attempts, updated_at FROM jobs WHERE job_key = ?", (key,)
    )
    if row is not None and row["state"] == "paused":
        # Parked until the daily budget resets (the app resumes it then); asking again won't help.
        return failure_message(row["error"])
    if row is None or row["state"] != "failed":
        return None
    cooldown = min(
        BASE_COOLDOWN_SECONDS * 2 ** max(int(row["attempts"]) - 1, 0), MAX_COOLDOWN_SECONDS
    )
    if _age_seconds(str(row["updated_at"])) >= cooldown:
        return None
    return failure_message(row["error"])


def not_ingested(svc: Services, paper_id: str) -> JSONResponse | None:
    """For endpoints that need the paper's text: 404 if unknown, 502 if reading it failed, 202
    while it is still being read (its ingest job queues the next steps itself). None if ready."""
    if svc.paper(paper_id) is not None:
        return None
    row = svc.db.fetchone(
        "SELECT state, error FROM jobs WHERE job_key = ?", (f"ingest:{paper_id}",)
    )
    if row is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "not_found",
                    "message": "Portolan hasn't seen this paper yet. Paste it first.",
                }
            },
        )
    if row["state"] == "failed":
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "ingest_failed", "message": ingest_message(row["error"])}},
        )
    return JSONResponse({"status": "generating"}, status_code=202)


def ingest_message(error: str | None) -> str:
    text = (error or "").strip()
    if "FetchError" in text or "IngestError" in text:
        detail = text.split(":", 1)[-1].strip()[:240]
        return f"Couldn't read the paper ({detail}). Try again, or upload the PDF."
    return failure_message(error)


def failures(svc: Services, paper_id: str) -> dict[str, str]:
    """Failed jobs of a paper by kind -> plain message (for the paper strip)."""
    rows = svc.db.fetchall(
        "SELECT kind, error FROM jobs WHERE paper_id = ? AND state = 'failed' ORDER BY updated_at",
        (paper_id,),
    )
    return {
        str(r["kind"]): (ingest_message if r["kind"] == "ingest" else failure_message)(r["error"])
        for r in rows
    }


def submit_or_report(
    svc: Services,
    kind: str,
    key: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: Literal["P0", "P1", "P2"],
    paper_id: str,
    front: bool = False,
) -> JSONResponse:
    """202 while the job runs; 502 with the reason if it failed recently; otherwise (re)queue."""
    reason = recent_failure(svc, key)
    if reason is not None:
        return JSONResponse(
            status_code=502, content={"error": {"code": "generation_failed", "message": reason}}
        )
    svc.queue.submit(kind, key, payload, priority=priority, paper_id=paper_id, front=front)
    return JSONResponse({"status": "generating"}, status_code=202)
