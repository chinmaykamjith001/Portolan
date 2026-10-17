"""Persistent priority job queue (spec §5.2 rules 5 and 9, §8, §10.5.2).

P0 (user-initiated) is dispatched before P1 (prefetch), which is dispatched before P2 (background
verification and FAQ). Preemption is at dispatch time: a running job is never interrupted, but no
worker starts a lower-priority job while a higher-priority one is queued. Within a priority,
jobs bumped to the front go first, then submission order.

Every state transition is written to the `jobs` table, so a restarted process picks up whatever
was left queued or running. Jobs are keyed: submitting a key that is already queued or running
does not duplicate it.

The queue's mutating methods may be called from any thread; workers run on the loop `start()`
was awaited on.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from portolan.orchestrator.events import EventBus
from portolan.store.db import Database

Priority = Literal["P0", "P1", "P2"]
JobState = Literal["queued", "running", "done", "failed", "paused"]

PRIORITIES: tuple[Priority, ...] = ("P0", "P1", "P2")
JOB_STATES: tuple[JobState, ...] = ("queued", "running", "done", "failed", "paused")
_RANK: dict[str, int] = {p: i for i, p in enumerate(PRIORITIES)}


class PauseJob(Exception):
    """Raise (or subclass) from a handler to park the job as `paused` instead of `failed`.

    Paused jobs stay in the table and run again after `JobQueue.resume_jobs()`.
    """


@dataclass(slots=True)
class Job:
    key: str
    kind: str
    priority: Priority
    payload: dict[str, Any]
    paper_id: str | None = None
    state: JobState = "queued"
    attempts: int = 0
    error: str | None = None
    front: bool = False
    seq: int = 0  # in-process submission order (FIFO tiebreak)


Handler = Callable[[Job], Awaitable[None]]


class JobQueue:
    def __init__(self, db: Database, bus: EventBus | None = None, workers: int = 2) -> None:
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self._db = db
        self._bus = bus
        self._n_workers = workers
        self._handlers: dict[str, Handler] = {}
        self._lock = threading.RLock()
        # Only queued and running jobs live in memory; finished ones are in the table only.
        self._jobs: dict[str, Job] = {}
        # One heap per priority of (not_front, seq, key); stale entries are skipped lazily.
        self._heaps: dict[str, list[tuple[int, int, str]]] = {p: [] for p in PRIORITIES}
        self._paused: set[str] = set()
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._changed: asyncio.Event | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._stopping = False

    # -- configuration -------------------------------------------------------------------------
    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    # -- submission ----------------------------------------------------------------------------
    def submit(
        self,
        kind: str,
        key: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: Priority,
        paper_id: str | None = None,
        front: bool = False,
        force: bool = False,
    ) -> bool:
        """Enqueue a job. Returns True if this call queued it (new, re-queued or forced).

        - Already queued: not duplicated; raised to `priority` if that is higher (and moved to
          the front if `front`). Returns False.
        - Running: left alone. Returns False.
        - Done: not re-run unless `force`. Failed or paused: queued again.
        """
        _check_priority(priority)
        payload = payload or {}
        with self._lock:
            live = self._jobs.get(key)
            if live is not None:
                if live.state == "queued":
                    self._bump_locked(live, priority, front)
                return False
            row = self._db.fetchone("SELECT state, attempts FROM jobs WHERE job_key = ?", (key,))
            if row is not None and row["state"] == "done" and not force:
                return False
            attempts = int(row["attempts"]) if row is not None else 0
            job = Job(
                key=key,
                kind=kind,
                priority=priority,
                payload=payload,
                paper_id=paper_id,
                attempts=attempts,
                front=front,
            )
            now = _now()
            self._db.execute(
                "INSERT INTO jobs (job_key, paper_id, kind, priority, state, payload_json,"
                " attempts, error, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?,"
                " NULL, ?, ?) ON CONFLICT(job_key) DO UPDATE SET paper_id = excluded.paper_id,"
                " kind = excluded.kind, priority = excluded.priority, state = 'queued',"
                " payload_json = excluded.payload_json, error = NULL,"
                " updated_at = excluded.updated_at",
                (key, paper_id, kind, priority, json.dumps(payload), attempts, now, now),
            )
            self._enqueue_locked(job)
        self._signal()
        return True

    def bump(self, key: str, priority: Priority, *, front: bool = False) -> bool:
        """Raise a queued job to `priority` (never lowers it); `front` puts it ahead of the
        other jobs at its priority. Returns True if anything changed."""
        _check_priority(priority)
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.state != "queued":
                return False
            changed = self._bump_locked(job, priority, front)
        if changed:
            self._signal()
        return changed

    def resume_jobs(self, kind: str | None = None) -> int:
        """Re-queue `paused` jobs (all, or one kind). Returns how many were resumed."""
        with self._lock:
            sql = "SELECT * FROM jobs WHERE state = 'paused'"
            params: tuple[str, ...] = ()
            if kind is not None:
                sql += " AND kind = ?"
                params = (kind,)
            rows = self._db.fetchall(sql + " ORDER BY created_at, rowid", params)
            for row in rows:
                job = _job_from_row(row)
                job.state = "queued"
                job.error = None
                self._persist(job)
                self._enqueue_locked(job)
        if rows:
            self._signal()
        return len(rows)

    # -- priority-level pause ------------------------------------------------------------------
    def pause(self, priority: Priority) -> None:
        """Stop dispatching a priority level (running jobs finish). Lower levels keep going."""
        _check_priority(priority)
        with self._lock:
            self._paused.add(priority)
        self._signal()

    def resume(self, priority: Priority) -> None:
        _check_priority(priority)
        with self._lock:
            self._paused.discard(priority)
        self._signal()

    def is_paused(self, priority: Priority) -> bool:
        return priority in self._paused

    # -- inspection ----------------------------------------------------------------------------
    def get(self, key: str) -> Job | None:
        with self._lock:
            live = self._jobs.get(key)
            if live is not None:
                return live
            row = self._db.fetchone("SELECT * FROM jobs WHERE job_key = ?", (key,))
            return _job_from_row(row) if row is not None else None

    def stats(self) -> dict[str, dict[str, int]]:
        """Counts as {state: {priority: n}}, every state and priority present."""
        out: dict[str, dict[str, int]] = {s: {p: 0 for p in PRIORITIES} for s in JOB_STATES}
        rows = self._db.fetchall(
            "SELECT state, priority, COUNT(*) AS n FROM jobs GROUP BY state, priority"
        )
        for row in rows:
            by_priority = out.setdefault(str(row["state"]), {p: 0 for p in PRIORITIES})
            by_priority[str(row["priority"])] = int(row["n"])
        return out

    def is_idle(self) -> bool:
        """Nothing running and nothing dispatchable queued (paused levels don't count)."""
        with self._lock:
            return not any(
                j.state == "running" or j.priority not in self._paused for j in self._jobs.values()
            )

    async def wait_idle(self, timeout: float | None = 5.0) -> None:
        """Wait until `is_idle()`. Raises TimeoutError."""
        async with asyncio.timeout(timeout):
            while not self.is_idle():
                changed = self._changed
                if changed is None:
                    raise RuntimeError("JobQueue.start() has not been awaited")
                changed.clear()
                if self.is_idle():
                    break
                await changed.wait()

    # -- lifecycle -----------------------------------------------------------------------------
    async def start(self) -> None:
        """Re-queue jobs a previous process left `queued` or `running`, then start workers."""
        if self._workers:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._changed = asyncio.Event()
        self._stopping = False
        with self._lock:
            rows = self._db.fetchall(
                "SELECT * FROM jobs WHERE state IN ('queued', 'running') ORDER BY created_at, rowid"
            )
            for row in rows:
                if row["job_key"] in self._jobs:
                    continue
                job = _job_from_row(row)
                if job.state == "running":
                    job.state = "queued"
                    self._persist(job)
                self._enqueue_locked(job)
        self._workers = [
            asyncio.create_task(self._worker(), name=f"jobqueue-worker-{i}")
            for i in range(self._n_workers)
        ]
        self._signal()

    async def stop(self, timeout: float | None = 5.0) -> None:
        """Let running jobs finish (up to `timeout`), then cancel what is left.

        A cancelled job goes back to `queued` and runs again after the next `start()`.
        """
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        workers, self._workers = self._workers, []
        if workers:
            _, pending = await asyncio.wait(workers, timeout=timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self._loop = None

    # -- internals -----------------------------------------------------------------------------
    def _enqueue_locked(self, job: Job) -> None:
        self._seq += 1
        job.seq = self._seq
        job.state = "queued"
        self._jobs[job.key] = job
        self._push_locked(job)

    def _push_locked(self, job: Job) -> None:
        heapq.heappush(self._heaps[job.priority], (0 if job.front else 1, job.seq, job.key))

    def _bump_locked(self, job: Job, priority: Priority, front: bool) -> bool:
        changed = False
        if _RANK[priority] < _RANK[job.priority]:
            job.priority = priority
            self._db.execute(
                "UPDATE jobs SET priority = ?, updated_at = ? WHERE job_key = ?",
                (priority, _now(), job.key),
            )
            changed = True
        if front and priority == job.priority and not job.front:
            job.front = True
            changed = True
        if changed:
            self._push_locked(job)  # the old heap entry is now stale
        return changed

    def _take(self) -> Job | None:
        with self._lock:
            for priority in PRIORITIES:
                if priority in self._paused:
                    continue
                heap = self._heaps[priority]
                while heap:
                    not_front, seq, key = heapq.heappop(heap)
                    job = self._jobs.get(key)
                    if (
                        job is None
                        or job.state != "queued"
                        or job.priority != priority
                        or job.seq != seq
                        or (0 if job.front else 1) != not_front
                    ):
                        continue  # stale entry
                    job.state = "running"
                    job.attempts += 1
                    self._persist(job)
                    return job
            return None

    async def _worker(self) -> None:
        wake = self._wake
        assert wake is not None
        while not self._stopping:
            job = self._take()
            if job is None:
                wake.clear()
                job = self._take()
                if job is None:
                    await wake.wait()
                    continue
            self._signal()
            await self._run(job)

    async def _run(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        try:
            if handler is None:
                raise LookupError(f"no handler registered for job kind {job.kind!r}")
            await handler(job)
        except asyncio.CancelledError:
            self._finish(job, "queued", None)
            raise
        except Exception as exc:
            message = _describe(exc)
            if _is_pause(exc):
                self._finish(job, "paused", message)
            else:
                self._finish(job, "failed", message)
                if self._bus is not None and job.paper_id is not None:
                    self._bus.publish(
                        job.paper_id,
                        "error",
                        {"job_key": job.key, "kind": job.kind, "message": message},
                    )
        else:
            self._finish(job, "done", None)

    def _finish(self, job: Job, state: JobState, error: str | None) -> None:
        with self._lock:
            job.state = state
            job.error = error
            job.front = False
            self._persist(job)
            if state == "queued":
                self._seq += 1
                job.seq = self._seq
                self._push_locked(job)
            else:
                self._jobs.pop(job.key, None)
        self._signal()

    def _persist(self, job: Job) -> None:
        self._db.execute(
            "UPDATE jobs SET priority = ?, state = ?, attempts = ?, error = ?, updated_at = ?"
            " WHERE job_key = ?",
            (job.priority, job.state, job.attempts, job.error, _now(), job.key),
        )

    def _signal(self) -> None:
        """Wake idle workers and `wait_idle` waiters, from whichever thread we are on."""
        loop = self._loop
        if loop is None:
            return
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            self._set_events()
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._set_events)

    def _set_events(self) -> None:
        if self._wake is not None:
            self._wake.set()
        if self._changed is not None:
            self._changed.set()


def _check_priority(priority: str) -> None:
    if priority not in _RANK:
        raise ValueError(f"unknown priority {priority!r}; expected one of {PRIORITIES}")


def _is_pause(exc: BaseException) -> bool:
    if isinstance(exc, PauseJob):
        return True
    # Matched by name so the queue need not import the LLM layer (llm/ratelimit.py).
    return any(cls.__name__ == "DailyBudgetExhausted" for cls in type(exc).__mro__)


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _job_from_row(row: Any) -> Job:
    payload = cast(dict[str, Any], json.loads(row["payload_json"]))
    return Job(
        key=str(row["job_key"]),
        kind=str(row["kind"]),
        priority=cast(Priority, row["priority"]),
        payload=payload,
        paper_id=row["paper_id"],
        state=cast(JobState, row["state"]),
        attempts=int(row["attempts"]),
        error=row["error"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
