"""Orchestrator: the persistent priority job queue and the SSE event bus."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from portolan.orchestrator.events import Event, EventBus, Subscription, format_sse, heartbeat
from portolan.orchestrator.queue import Job, JobQueue, PauseJob
from portolan.store.db import Database, open_database


@pytest.fixture
def db() -> Iterator[Database]:
    database = open_database(":memory:")
    yield database
    database.close()


@pytest.fixture
async def stopper() -> AsyncIterator[list[JobQueue]]:
    """Queues appended here are stopped at teardown even if the test fails."""
    queues: list[JobQueue] = []
    yield queues
    for q in queues:
        await q.stop(timeout=0.1)


def recorder(order: list[str]) -> Callable[[Job], Awaitable[None]]:
    async def handle(job: Job) -> None:
        order.append(job.key)

    return handle


async def take(sub: Subscription, n: int) -> list[Event]:
    return [await asyncio.wait_for(anext(sub), 1.0) for _ in range(n)]


def state(db: Database, key: str) -> str:
    row = db.fetchone("SELECT state FROM jobs WHERE job_key = ?", (key,))
    assert row is not None
    return str(row["state"])


# -- queue ordering ----------------------------------------------------------------------------


async def test_priority_order_single_worker(db: Database, stopper: list[JobQueue]) -> None:
    order: list[str] = []
    q = JobQueue(db, workers=1)
    stopper.append(q)
    q.register("k", recorder(order))
    q.submit("k", "p2-a", priority="P2")
    q.submit("k", "p1-a", priority="P1")
    q.submit("k", "p2-b", priority="P2")
    q.submit("k", "p0-a", priority="P0")
    q.submit("k", "p1-b", priority="P1")
    await q.start()
    await q.wait_idle(1.0)
    assert order == ["p0-a", "p1-a", "p1-b", "p2-a", "p2-b"]
    assert q.stats()["done"] == {"P0": 1, "P1": 2, "P2": 2}


async def test_p0_preempts_queued_p1(db: Database, stopper: list[JobQueue]) -> None:
    order: list[str] = []
    gate = asyncio.Event()
    started = asyncio.Event()

    async def handle(job: Job) -> None:
        if job.key == "gate":
            started.set()
            await gate.wait()
        order.append(job.key)

    q = JobQueue(db, workers=1)
    stopper.append(q)
    q.register("k", handle)
    await q.start()
    q.submit("k", "gate", priority="P1")
    await asyncio.wait_for(started.wait(), 1.0)
    q.submit("k", "p1-a", priority="P1")
    q.submit("k", "p1-b", priority="P1")
    q.submit("k", "p0", priority="P0")
    gate.set()
    await q.wait_idle(1.0)
    assert order == ["gate", "p0", "p1-a", "p1-b"]


async def test_dedupe_bump_and_done_not_rerun(db: Database, stopper: list[JobQueue]) -> None:
    order: list[str] = []
    q = JobQueue(db, workers=1)
    stopper.append(q)
    q.register("k", recorder(order))
    assert q.submit("k", "a", {"x": 1}, priority="P2") is True
    assert q.submit("k", "a", {"x": 2}, priority="P2") is False
    q.submit("k", "b", priority="P1")
    assert q.submit("k", "a", priority="P0") is False  # deduped, but bumped to P0
    assert q.bump("a", "P2") is False  # never lowers
    job = q.get("a")
    assert job is not None and job.priority == "P0" and job.payload == {"x": 1}
    row = db.fetchone("SELECT COUNT(*) AS n, MAX(priority) AS p FROM jobs WHERE job_key = 'a'")
    assert row is not None and row["n"] == 1 and row["p"] == "P0"

    await q.start()
    await q.wait_idle(1.0)
    assert order == ["a", "b"]

    assert q.submit("k", "a", priority="P0") is False  # done: not re-run
    assert q.submit("k", "a", priority="P0", force=True) is True
    await q.wait_idle(1.0)
    assert order == ["a", "b", "a"]
    done = q.get("a")
    assert done is not None and done.state == "done" and done.attempts == 2


async def test_submit_while_running_is_ignored(db: Database, stopper: list[JobQueue]) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()
    runs: list[str] = []

    async def handle(job: Job) -> None:
        runs.append(job.key)
        started.set()
        await gate.wait()

    q = JobQueue(db, workers=2)
    stopper.append(q)
    q.register("k", handle)
    await q.start()
    q.submit("k", "a", priority="P1")
    await asyncio.wait_for(started.wait(), 1.0)
    assert q.submit("k", "a", priority="P0") is False
    assert q.bump("a", "P0") is False
    gate.set()
    await q.wait_idle(1.0)
    assert runs == ["a"]


async def test_front_bump(db: Database, stopper: list[JobQueue]) -> None:
    order: list[str] = []
    q = JobQueue(db, workers=1)
    stopper.append(q)
    q.register("k", recorder(order))
    for key in ("a", "b", "c", "d"):
        q.submit("k", key, priority="P1")
    q.submit("k", "z", priority="P2")
    assert q.bump("d", "P1", front=True) is True
    assert q.bump("d", "P1", front=True) is False  # already at the front
    assert q.bump("z", "P1", front=True) is True  # raised and fronted in one step
    await q.start()
    await q.wait_idle(1.0)
    assert order == ["d", "z", "a", "b", "c"]


async def test_submit_front(db: Database, stopper: list[JobQueue]) -> None:
    order: list[str] = []
    q = JobQueue(db, workers=1)
    stopper.append(q)
    q.register("k", recorder(order))
    q.submit("k", "a", priority="P1")
    q.submit("k", "b", priority="P1", front=True)
    q.submit("k", "c", priority="P0")
    await q.start()
    await q.wait_idle(1.0)
    assert order == ["c", "b", "a"]


async def test_pause_and_resume_p2(db: Database, stopper: list[JobQueue]) -> None:
    order: list[str] = []
    q = JobQueue(db, workers=2)
    stopper.append(q)
    q.register("k", recorder(order))
    q.pause("P2")
    assert q.is_paused("P2")
    q.submit("k", "bg", priority="P2")
    q.submit("k", "pre", priority="P1")
    q.submit("k", "user", priority="P0")
    await q.start()
    await q.wait_idle(1.0)  # paused levels don't count as pending work
    assert sorted(order) == ["pre", "user"]
    assert state(db, "bg") == "queued"
    assert q.stats()["queued"]["P2"] == 1

    q.resume("P2")
    await q.wait_idle(1.0)
    assert order[-1] == "bg"
    assert state(db, "bg") == "done"


# -- persistence -------------------------------------------------------------------------------


async def test_restart_recovers_queued_and_running(tmp_path: Path) -> None:
    path = tmp_path / "portolan.db"
    db1 = open_database(path)
    q1 = JobQueue(db1)
    q1.submit("k", "a", {"n": 1}, priority="P1", paper_id="paper")
    q1.submit("k", "b", priority="P0")
    q1.submit("k", "c", priority="P2")
    db1.execute("UPDATE jobs SET state = 'running', attempts = 1 WHERE job_key = 'b'")  # crashed
    db1.execute("UPDATE jobs SET state = 'done' WHERE job_key = 'c'")
    db1.close()

    db2 = open_database(path)
    seen: list[Job] = []

    async def handle(job: Job) -> None:
        seen.append(job)

    q2 = JobQueue(db2, workers=1)
    q2.register("k", handle)
    try:
        await q2.start()
        await q2.wait_idle(1.0)
    finally:
        await q2.stop()
    assert [j.key for j in seen] == ["b", "a"]
    assert seen[1].payload == {"n": 1} and seen[1].paper_id == "paper"
    assert seen[0].attempts == 2
    assert {state(db2, k) for k in ("a", "b", "c")} == {"done"}
    db2.close()


async def test_stop_cancels_and_requeues(db: Database) -> None:
    started = asyncio.Event()

    async def hang(job: Job) -> None:
        started.set()
        await asyncio.Event().wait()

    q = JobQueue(db, workers=1)
    q.register("k", hang)
    await q.start()
    q.submit("k", "a", priority="P1")
    await asyncio.wait_for(started.wait(), 1.0)
    assert state(db, "a") == "running"
    await q.stop(timeout=0.02)
    assert state(db, "a") == "queued"

    order: list[str] = []
    q.register("k", recorder(order))
    await q.start()
    try:
        await q.wait_idle(1.0)
    finally:
        await q.stop()
    assert order == ["a"]


# -- failure and paused states ------------------------------------------------------------------


class DailyBudgetExhausted(Exception):
    """Stand-in matched by class name, like portolan.llm.ratelimit.DailyBudgetExhausted."""


class OverBudget(DailyBudgetExhausted):
    pass


class WaitForUser(PauseJob):
    pass


async def test_failure_marks_failed_and_publishes_error(
    db: Database, stopper: list[JobQueue]
) -> None:
    bus = EventBus(first_id=1)
    sub = bus.subscribe("paper")

    async def boom(job: Job) -> None:
        raise ValueError("bad input")

    q = JobQueue(db, bus=bus)
    stopper.append(q)
    q.register("k", boom)
    q.submit("k", "a", priority="P1", paper_id="paper")
    q.submit("k", "b", priority="P1")  # no paper_id: failed, but no event
    q.submit("other", "c", priority="P1", paper_id="paper")  # no handler
    await q.start()
    await q.wait_idle(1.0)

    a, c = q.get("a"), q.get("c")
    assert a is not None and a.state == "failed" and a.error == "ValueError: bad input"
    assert state(db, "b") == "failed"
    assert c is not None and c.state == "failed" and c.error is not None
    assert "no handler" in c.error
    events = await take(sub, 2)
    assert {e.type for e in events} == {"error"}
    assert {e.data["job_key"] for e in events} == {"a", "c"}
    sub.close()

    # A failed job can be submitted again.
    q.register("k", recorder([]))
    assert q.submit("k", "a", priority="P1") is True
    await q.wait_idle(1.0)
    assert state(db, "a") == "done"


@pytest.mark.parametrize("exc_type", [DailyBudgetExhausted, OverBudget, PauseJob, WaitForUser])
async def test_pause_exceptions_park_job(
    db: Database, stopper: list[JobQueue], exc_type: type[Exception]
) -> None:
    bus = EventBus()
    sub = bus.subscribe("paper")
    attempts: list[int] = []

    async def handle(job: Job) -> None:
        attempts.append(job.attempts)
        if len(attempts) == 1:
            raise exc_type("budget gone")

    q = JobQueue(db, bus=bus)
    stopper.append(q)
    q.register("verify", handle)
    q.register("faq", recorder([]))
    q.submit("verify", "v", priority="P2", paper_id="paper")
    await q.start()
    await q.wait_idle(1.0)
    job = q.get("v")
    assert job is not None and job.state == "paused"
    assert job.error == f"{exc_type.__name__}: budget gone"
    assert q.stats()["paused"]["P2"] == 1
    assert bus.recent("paper") == []  # pausing is not an error

    assert q.resume_jobs("faq") == 0
    assert q.resume_jobs() == 1
    await q.wait_idle(1.0)
    assert state(db, "v") == "done"
    assert attempts == [1, 2]
    sub.close()


# -- concurrency -------------------------------------------------------------------------------


async def test_concurrent_submits_stay_consistent(db: Database, stopper: list[JobQueue]) -> None:
    counts: dict[str, int] = {}

    async def handle(job: Job) -> None:
        counts[job.key] = counts.get(job.key, 0) + 1
        await asyncio.sleep(0)

    q = JobQueue(db, workers=4)
    stopper.append(q)
    q.register("k", handle)
    await q.start()
    priorities = ("P0", "P1", "P2")

    async def from_loop(i: int) -> bool:
        return q.submit("k", f"job-{i % 40}", priority=priorities[i % 3])

    async def from_thread(i: int) -> bool:
        return await asyncio.to_thread(
            q.submit, "k", f"job-{i % 40}", None, priority=priorities[i % 3]
        )

    results = await asyncio.gather(*(from_loop(i) if i % 2 else from_thread(i) for i in range(100)))
    await q.wait_idle(2.0)
    rows = db.fetchall("SELECT job_key, state FROM jobs")
    assert len(rows) == 40
    assert {r["state"] for r in rows} == {"done"}
    # Every key ran at least once; a key re-runs only if a submit landed after it finished.
    assert set(counts) == {f"job-{i}" for i in range(40)}
    assert sum(counts.values()) == sum(results)
    assert sum(q.stats()["done"].values()) == 40
    assert q.is_idle()


# -- events / SSE ------------------------------------------------------------------------------


def test_format_sse_and_heartbeat() -> None:
    bus = EventBus(first_id=7)
    event = bus.publish("p", "rate_limited", {"model": "flash", "wait_seconds": 20, "msg": "é"})
    assert event.id == 7 and event.paper_id == "p" and event.ts > 0
    assert bus.publish("p", "progress", {}).id == 8
    assert format_sse(event) == (
        'id: 7\nevent: rate_limited\ndata: {"model":"flash","wait_seconds":20,"msg":"é"}\n\n'
    )
    beat = heartbeat()
    assert beat.startswith(":") and beat.endswith("\n\n")
    with pytest.raises(ValueError, match="unknown event type"):
        bus.publish("p", "layer-ready", {})


def test_event_ids_increase_across_bus_instances() -> None:
    a = EventBus().publish("p", "progress", {})
    b = EventBus().publish("p", "progress", {})
    assert b.id >= a.id


async def test_replay_after_last_event_id() -> None:
    bus = EventBus(first_id=1, ring_size=5)
    for i in range(3):
        bus.publish("p", "progress", {"i": i})
    bus.publish("other", "progress", {"i": 99})
    sub = bus.subscribe("p", last_event_id=1)
    live = bus.publish("p", "layer_ready", {"layer": "L1"})
    got = await take(sub, 3)
    assert [e.id for e in got] == [2, 3, live.id]
    assert [e.data.get("i") for e in got[:2]] == [1, 2]
    sub.close()

    # A fresh subscriber without Last-Event-ID gets only live events.
    fresh = bus.subscribe("p")
    bus.publish("p", "faq_ready", {})
    assert [e.type for e in await take(fresh, 1)] == ["faq_ready"]
    fresh.close()

    # An id older than the ring replays the whole ring (the most recent ring_size events).
    for i in range(10):
        bus.publish("p", "progress", {"i": i})
    old = bus.subscribe("p", last_event_id=0)
    replayed = await take(old, 5)
    assert [e.data["i"] for e in replayed] == [5, 6, 7, 8, 9]
    old.close()


async def test_slow_subscriber_drops_oldest_without_blocking() -> None:
    bus = EventBus(first_id=1, queue_size=3)
    slow = bus.subscribe("p")
    fast = bus.subscribe("p")
    fast_seen: list[int] = []
    for i in range(10):
        bus.publish("p", "progress", {"i": i})
        fast_seen.extend(e.id for e in await take(fast, 1))
    assert fast_seen == list(range(1, 11))
    assert fast.dropped == 0
    assert [e.id for e in await take(slow, 3)] == [8, 9, 10]
    assert slow.dropped == 7
    slow.close()
    fast.close()


async def test_close_ends_iteration_and_unsubscribes() -> None:
    bus = EventBus()
    async with bus.subscribe("p") as sub:
        assert bus.subscriber_count("p") == 1

        async def drain() -> list[Event]:
            return [e async for e in sub]

        task = asyncio.create_task(drain())
        await asyncio.sleep(0)
        bus.publish("p", "concept_updated", {"id": "c1"})
        await asyncio.sleep(0)
        sub.close()
        events = await asyncio.wait_for(task, 1.0)
    assert [e.type for e in events] == ["concept_updated"]
    assert bus.subscriber_count("p") == 0


async def test_publish_from_another_thread() -> None:
    bus = EventBus()
    sub = bus.subscribe("p")
    await asyncio.to_thread(bus.publish, "p", "finding_added", {"id": "f1"})
    got = await take(sub, 1)
    assert got[0].type == "finding_added"
    sub.close()


async def test_a_p0_worker_serves_clicks_while_background_jobs_hold_the_others(
    db: Database, stopper: list[JobQueue]
) -> None:
    queue = JobQueue(db, workers=2, p0_workers=1)
    stopper.append(queue)
    release = asyncio.Event()
    clicked = asyncio.Event()

    async def slow(_job: Job) -> None:
        await release.wait()

    async def click(_job: Job) -> None:
        clicked.set()

    queue.register("slow", slow)
    queue.register("click", click)
    await queue.start()
    for i in range(3):  # more background work than regular workers
        queue.submit("slow", f"slow:{i}", priority="P1")
    await asyncio.sleep(0.05)
    queue.submit("click", "click:1", priority="P0")
    await asyncio.wait_for(clicked.wait(), timeout=2)  # didn't wait for any slow job
    release.set()


async def test_the_p0_worker_never_takes_background_work(
    db: Database, stopper: list[JobQueue]
) -> None:
    queue = JobQueue(db, workers=1, p0_workers=1)
    stopper.append(queue)
    running: list[str] = []
    release = asyncio.Event()

    async def slow(job: Job) -> None:
        running.append(job.key)
        await release.wait()

    queue.register("slow", slow)
    await queue.start()
    queue.submit("slow", "slow:a", priority="P1")
    queue.submit("slow", "slow:b", priority="P2")
    await asyncio.sleep(0.1)
    assert running == ["slow:a"]  # the second waits for the regular worker
    release.set()
