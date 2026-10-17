"""Polite, cached HTTP for external sources (spec §10.1, §15).

- Per-host minimum spacing between requests (arXiv: one request every ~3 s).
- A descriptive User-Agent.
- Every 200/404 response cached in SQLite, so re-ingesting a paper costs no requests.
- Retries 429/5xx and transport errors with backoff, honoring `retry-after`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from portolan import __version__
from portolan.llm.ratelimit import Clock, RealClock
from portolan.store.db import Database

ARXIV_SPACING = 3.0
HOST_SPACING: dict[str, float] = {
    "export.arxiv.org": ARXIV_SPACING,
    "arxiv.org": ARXIV_SPACING,
    "ar5iv.labs.arxiv.org": ARXIV_SPACING,
    "ar5iv.org": ARXIV_SPACING,
    "api.semanticscholar.org": 1.1,
}
DEFAULT_SPACING = 1.0
MAX_BYTES = 100 * 1024 * 1024
RETRY_STATUSES = {429, 500, 502, 503, 504}
CACHEABLE_STATUSES = {200, 404}
NOT_FOUND_TTL = 24 * 3600.0


def user_agent(contact: str = "") -> str:
    base = f"Portolan/{__version__} (research paper explorer; self-hosted"
    return f"{base}; {contact})" if contact else f"{base})"


class FetchError(Exception):
    """A plain-language network failure."""


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str | None
    from_cache: bool
    headers: dict[str, str] = field(default_factory=dict[str, str])  # live responses only

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _cache_key(url: str, params: dict[str, str] | None) -> str:
    raw = json.dumps([url, sorted((params or {}).items())])
    return hashlib.sha256(raw.encode()).hexdigest()


class PoliteClient:
    def __init__(
        self,
        db: Database | None,
        *,
        contact: str = "",
        clock: Clock | None = None,
        wall: Callable[[], float] = time.time,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
        spacing: dict[str, float] | None = None,
    ) -> None:
        self.db = db
        self.clock: Clock = clock or RealClock()
        self.wall = wall
        self.max_retries = max_retries
        self.spacing = {**HOST_SPACING, **(spacing or {})}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.requests_made = 0
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent(contact)},
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # -- cache ------------------------------------------------------------------------------

    def _cached(self, key: str, ttl: float | None) -> FetchResult | None:
        if self.db is None:
            return None
        row = self.db.fetchone(
            "SELECT url, status, content_type, body, fetched_at FROM http_cache WHERE key = ?",
            (key,),
        )
        if row is None:
            return None
        age = self.wall() - float(row["fetched_at"])
        limit = NOT_FOUND_TTL if row["status"] == 404 else ttl
        if limit is not None and age > limit:
            return None
        return FetchResult(
            url=row["url"],
            status=int(row["status"]),
            content=bytes(row["body"]),
            content_type=row["content_type"],
            from_cache=True,
        )

    def _store(self, key: str, result: FetchResult) -> None:
        if self.db is None or result.status not in CACHEABLE_STATUSES:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO http_cache (key, url, status, content_type, body, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, result.url, result.status, result.content_type, result.content, self.wall()),
        )

    # -- politeness -------------------------------------------------------------------------

    async def _wait_turn(self, host: str) -> None:
        gap = self.spacing.get(host, DEFAULT_SPACING)
        last = self._last.get(host)
        if last is not None:
            wait = last + gap - self.clock.now()
            if wait > 0:
                await self.clock.sleep(wait)
        self._last[host] = self.clock.now()

    # -- API --------------------------------------------------------------------------------

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cache: bool = True,
        ttl: float | None = None,
    ) -> FetchResult:
        """GET with politeness and caching. `ttl=None` caches 200s forever (versioned content)."""
        key = _cache_key(url, params)
        if cache:
            hit = self._cached(key, ttl)
            if hit is not None:
                return hit

        host = urlsplit(url).hostname or ""
        lock = self._locks.setdefault(host, asyncio.Lock())
        last_error = "no response"
        async with lock:
            for attempt in range(self.max_retries + 1):
                await self._wait_turn(host)
                self.requests_made += 1
                try:
                    resp = await self._client.get(url, params=params, headers=headers)
                except httpx.TransportError as exc:
                    last_error = type(exc).__name__
                    await self.clock.sleep(2.0 * 2**attempt)
                    continue
                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    retry_after = resp.headers.get("retry-after", "")
                    delay = float(retry_after) if retry_after.isdigit() else 2.0 * 2**attempt
                    await self.clock.sleep(min(delay, 60.0))
                    last_error = f"HTTP {resp.status_code}"
                    continue
                if len(resp.content) > MAX_BYTES:
                    raise FetchError(f"The response from {host} was too large to download.")
                result = FetchResult(
                    url=str(resp.url),
                    status=resp.status_code,
                    content=resp.content,
                    content_type=resp.headers.get("content-type"),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    from_cache=False,
                )
                if cache:
                    self._store(key, result)
                return result
        raise FetchError(f"Couldn't reach {host} ({last_error}). Check your connection and retry.")
