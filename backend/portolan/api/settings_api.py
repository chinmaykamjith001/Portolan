"""Settings endpoint (spec M11): what the Settings popover shows. Never any key or env value."""

from __future__ import annotations

from fastapi import APIRouter, Request

from portolan import __version__
from portolan.api.papers import services

router = APIRouter(prefix="/api")


@router.get("/settings")
def settings(request: Request) -> dict[str, object]:
    config = services(request).config
    models = config.llm.models
    return {
        "version": __version__,
        "provider": config.llm.provider,
        "models": {"fast": models.fast, "default": models.default, "strong": models.strong},
        "limits": {
            "rpm": config.llm.limits.rpm,
            "rpd": config.llm.limits.rpd,
            "tpm": config.llm.limits.tpm,
        },
        "free_tier_notice": config.llm.provider == "gemini",
        "sandbox_mode": config.sandbox.mode,
        "repo_linked": False,
    }
