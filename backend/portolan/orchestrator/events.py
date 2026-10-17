"""In-process event bus that feeds the per-paper SSE stream (spec §12).

Publishers never block: each subscriber has a bounded queue and, when a slow subscriber falls
behind, its oldest undelivered events are dropped. A ring buffer of recent events per paper lets a
reconnecting browser send `Last-Event-ID` and replay what it missed.

`publish` is safe to call from any thread (FastAPI runs sync handlers in a threadpool); delivery
to a subscriber always happens on the event loop that subscriber was created on.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Self

EVENT_TYPES: frozenset[str] = frozenset(
    {
        "progress",
        "layer_ready",
        "concept_updated",
        "detail_ready",
        "route_ready",
        "finding_added",
        "faq_ready",
        "rate_limited",
        "error",
    }
)

RING_SIZE = 500
SUBSCRIBER_QUEUE_SIZE = 256


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    paper_id: str
    type: str
    data: dict[str, Any]
    ts: float  # unix seconds


class Subscription:
    """An async iterator of one paper's events. Registered as soon as it is created.

    Use it as `async with bus.subscribe(pid) as sub: async for event in sub: ...`, or iterate it
    directly and call `close()` when done.
    """

    def __init__(self, bus: EventBus, paper_id: str, backlog: list[Event], queue_size: int) -> None:
        self._bus = bus
        self.paper_id = paper_id
        self._backlog: deque[Event] = deque(backlog)
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=queue_size)
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._closed = False
        self.dropped = 0  # events lost because this subscriber was too slow

    # -- delivery (called by the bus, under its lock) ------------------------------------------
    def _deliver(self, event: Event | None) -> None:
        loop = self._loop
        if loop is None or _running_loop() is loop:
            self._push(event)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._push, event)

    def _push(self, event: Event | None) -> None:
        while self._queue.full():
            dropped = self._queue.get_nowait()
            if dropped is not None:
                self.dropped += 1
        self._queue.put_nowait(event)

    # -- consumption ---------------------------------------------------------------------------
    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Event:
        if self._backlog:
            return self._backlog.popleft()
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unsubscribe(self)  # pyright: ignore[reportPrivateUsage]
        self._deliver(None)  # wake a consumer blocked in __anext__

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class EventBus:
    def __init__(
        self,
        *,
        ring_size: int = RING_SIZE,
        queue_size: int = SUBSCRIBER_QUEUE_SIZE,
        first_id: int | None = None,
    ) -> None:
        self._ring_size = ring_size
        self._queue_size = queue_size
        # Seed ids from the wall clock (ms) so they keep increasing across server restarts and a
        # browser's stale Last-Event-ID never hides fresh events.
        self._last_id = int(time.time() * 1000) if first_id is None else first_id - 1
        self._rings: dict[str, deque[Event]] = {}
        self._subs: dict[str, set[Subscription]] = {}
        self._lock = threading.Lock()

    @property
    def last_id(self) -> int:
        return self._last_id

    def publish(self, paper_id: str, type: str, data: dict[str, Any]) -> Event:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}; expected one of {sorted(EVENT_TYPES)}")
        with self._lock:
            self._last_id += 1
            event = Event(id=self._last_id, paper_id=paper_id, type=type, data=data, ts=time.time())
            ring = self._rings.get(paper_id)
            if ring is None:
                ring = self._rings[paper_id] = deque(maxlen=self._ring_size)
            ring.append(event)
            for sub in self._subs.get(paper_id, ()):
                sub._deliver(event)  # pyright: ignore[reportPrivateUsage]
        return event

    def subscribe(self, paper_id: str, last_event_id: int | None = None) -> Subscription:
        """Subscribe to a paper's events. With `last_event_id`, first replay newer buffered events.

        If the id is older than the ring buffer, the whole buffer is replayed (the gap is lost).
        """
        with self._lock:
            backlog: list[Event] = []
            if last_event_id is not None:
                backlog = [e for e in self._rings.get(paper_id, ()) if e.id > last_event_id]
            sub = Subscription(self, paper_id, backlog, self._queue_size)
            self._subs.setdefault(paper_id, set()).add(sub)
        return sub

    def recent(self, paper_id: str) -> list[Event]:
        with self._lock:
            return list(self._rings.get(paper_id, ()))

    def subscriber_count(self, paper_id: str) -> int:
        with self._lock:
            return len(self._subs.get(paper_id, ()))

    def _unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            subs = self._subs.get(sub.paper_id)
            if subs is not None:
                subs.discard(sub)
                if not subs:
                    del self._subs[sub.paper_id]


def format_sse(event: Event) -> str:
    """One SSE frame: `id:`, `event:` and a single-line JSON `data:`, then a blank line."""
    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"


def heartbeat() -> str:
    """An SSE comment frame; keeps proxies from closing an idle stream. Browsers ignore it."""
    return ": heartbeat\n\n"


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
