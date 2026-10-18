from __future__ import annotations

from pathlib import Path

from portolan.evalharness.checkpoint import Checkpoint, RecordResult
from portolan.evalharness.records import EvalRecord
from portolan.evalharness.runner import (
    BudgetExhaustedStop,
    EngineOutput,
    EvalError,
    results_for,
    run,
)
from portolan.llm.ratelimit import DailyBudgetExhausted
from tests.eval_helpers import finding, gold, record


class ScriptedEngine:
    """Does what the test says per record id and remembers every call."""

    def __init__(self, version: str = "v1", script: dict[str, Exception] | None = None) -> None:
        self.version = version
        self.script = script or {}
        self.calls: list[str] = []

    async def check(self, rec: EvalRecord) -> EngineOutput:
        self.calls.append(rec.id)
        if rec.id in self.script:
            raise self.script[rec.id]
        return EngineOutput(findings=[finding(f"f-{rec.id}")], requests_used=3, note=None)


def records(n: int = 4) -> list[EvalRecord]:
    return [record(f"r{i}", [gold("g", "something differs")]) for i in range(n)]


async def test_every_record_runs_and_is_checkpointed(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    engine = ScriptedEngine()
    summary = await run(records(), engine, ck)
    assert (summary.ran, summary.failed, summary.already_done) == (4, 0, 0)
    saved = ck.load()
    assert {k[0] for k in saved} == {"r0", "r1", "r2", "r3"}
    assert all(v.status == "done" and v.requests_used == 3 for v in saved.values())


async def test_resume_skips_finished_records(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    stopper = ScriptedEngine(script={"r2": BudgetExhaustedStop()})
    first = await run(records(), stopper, ck)
    assert first.stopped_for_budget and first.ran == 2 and first.pending == 2
    assert stopper.calls == ["r0", "r1", "r2"]  # r3 was never started

    again = ScriptedEngine()
    second = await run(records(), again, ck)
    assert again.calls == ["r2", "r3"]
    assert (second.ran, second.already_done, second.stopped_for_budget) == (2, 2, False)


async def test_gateway_budget_error_stops_the_run_cleanly(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    engine = ScriptedEngine(script={"r1": DailyBudgetExhausted("m", 1400, 1400, "P1")})
    messages: list[str] = []
    summary = await run(records(), engine, ck, progress=messages.append)
    assert summary.stopped_for_budget and summary.ran == 1
    assert any("run the same command tomorrow" in m for m in messages)
    assert ("r1", "v1") not in ck.load()  # unfinished work is not recorded as a result


async def test_one_failing_record_does_not_stop_the_others(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    engine = ScriptedEngine(script={"r1": EvalError("Couldn't download the code.\nTraceback...")})
    summary = await run(records(), engine, ck)
    assert (summary.ran, summary.failed) == (3, 1)
    failed = ck.load()[("r1", "v1")]
    assert failed.status == "failed" and failed.error == "Couldn't download the code."


async def test_failed_records_are_retried_unless_told_not_to(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    await run(records(2), ScriptedEngine(script={"r0": ValueError("boom")}), ck)

    skip = ScriptedEngine()
    await run(records(2), skip, ck, retry_failed=False)
    assert skip.calls == []

    retry = ScriptedEngine()
    summary = await run(records(2), retry, ck)
    assert retry.calls == ["r0"] and summary.ran == 1
    assert ck.load()[("r0", "v1")].status == "done"


async def test_a_new_engine_version_does_not_reuse_old_results(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    await run(records(2), ScriptedEngine("v1"), ck)
    newer = ScriptedEngine("v2")
    await run(records(2), newer, ck)
    assert newer.calls == ["r0", "r1"]
    assert {k[1] for k in ck.load()} == {"v1", "v2"}
    assert set(results_for(records(2), ck, "v2")) == {"r0", "r1"}


def test_a_torn_last_line_from_a_crash_is_ignored(tmp_path: Path) -> None:
    ck = Checkpoint(tmp_path / "res.jsonl")
    ck.append(RecordResult(record_id="r0", engine_version="v1", status="done"))
    with ck.path.open("a", encoding="utf-8") as fh:
        fh.write('{"record_id": "r1", "engine_vers')
    assert set(ck.load()) == {("r0", "v1")}
