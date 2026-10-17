"""Gemini helpers that are not LLM calls (model listing for `portolan doctor`)."""

from __future__ import annotations

import re
from typing import Any

import httpx

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


async def list_models(api_key: str, client: httpx.AsyncClient | None = None) -> list[str]:
    """Return model ids (without the `models/` prefix) that support generateContent.

    The key goes in the `x-goog-api-key` header, never in the URL, so it can't leak into logs.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        names: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(MODELS_URL, params=params, headers={"x-goog-api-key": api_key})
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            for m in body.get("models", []):
                methods: list[str] = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    names.append(str(m["name"]).removeprefix("models/"))
            page_token = body.get("nextPageToken")
            if not page_token:
                return sorted(names)
    finally:
        if own_client:
            await client.aclose()


def _best(names: list[str], pattern: str) -> str | None:
    best: tuple[float, str] | None = None
    for name in names:
        m = re.fullmatch(pattern, name)
        if m:
            version = float(m.group(1))
            if best is None or version > best[0]:
                best = (version, name)
    return best[1] if best else None


def suggest_models(names: list[str]) -> dict[str, str]:
    """Pick the newest stable Flash-Lite and Flash models. Pro is never suggested (free tier)."""
    flash_lite = _best(names, r"gemini-(\d+(?:\.\d+)?)-flash-lite")
    flash = _best(names, r"gemini-(\d+(?:\.\d+)?)-flash")
    suggestion: dict[str, str] = {}
    if flash_lite or flash:
        suggestion["fast"] = f"gemini/{flash_lite or flash}"
    if flash:
        suggestion["default"] = f"gemini/{flash}"
        suggestion["strong"] = f"gemini/{flash}"
    return suggestion
