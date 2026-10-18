"""`portolan eval`: load, sample, estimate, run, score, report."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import date
from pathlib import Path

import typer

from portolan import config as config_module
from portolan.config import Config, api_key_present, load_config
from portolan.evalharness.adapters import DatasetError, inspect_dataset, load_records
from portolan.evalharness.checkpoint import Checkpoint
from portolan.evalharness.estimate import describe, estimate, needs_confirmation
from portolan.evalharness.fake import FakeEngine
from portolan.evalharness.real import RealEngine
from portolan.evalharness.records import EvalRecord
from portolan.evalharness.report import ReportMeta, render_report, write_report
from portolan.evalharness.runner import Engine, RunSummary, results_for, run
from portolan.evalharness.sampling import sample_records
from portolan.evalharness.scoring import DefaultMatcher, score_records
from portolan.ingest.http import PoliteClient
from portolan.llm.gateway import Gateway
from portolan.store.db import Database, open_database

SPLITS = ("all", "real", "synthetic")
SAMPLE_DIR = Path(__file__).resolve().parents[3] / "eval" / "scicoqa" / "sample"
DOWNLOAD_HINT = (
    "Get the dataset first: `git clone https://github.com/UKPLab/scicoqa`, then pass the folder "
    "with --data. Run `portolan eval --inspect PATH` to check how its fields are read."
)


def run_eval(
    *,
    data: str | None,
    limit: int,
    split: str,
    seed: int,
    out: str | None,
    report_dir: str | None,
    yes: bool,
    fresh: bool,
    dry_run: bool,
    inspect: str | None,
    retry_failed: bool,
) -> None:
    if inspect:
        try:
            typer.echo(inspect_dataset(Path(inspect)))
        except DatasetError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from None
        return
    if split not in SPLITS:
        typer.echo(f"--split must be one of: {', '.join(SPLITS)}.")
        raise typer.Exit(code=1)
    if data is None and not dry_run:
        typer.echo(f"--data is required. {DOWNLOAD_HINT}")
        raise typer.Exit(code=1)
    data_path = Path(data) if data else SAMPLE_DIR
    try:
        loaded = load_records(data_path)
    except DatasetError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Read {loaded.rows} row(s) from {len(loaded.files)} file(s) -> {len(loaded.records)} "
        "record(s). The dataset format is UNVERIFIED; see eval/README.md."
    )
    for line in loaded.skipped[:5]:
        typer.echo(f"  skipped {line}")
    if len(loaded.skipped) > 5:
        typer.echo(f"  ... and {len(loaded.skipped) - 5} more skipped rows")

    sampled = sample_records(loaded.records, limit=limit, seed=seed, split=split)
    if not sampled:
        typer.echo(f"No records in split '{split}'.")
        raise typer.Exit(code=1)

    if out:
        runs_dir = Path(out)
    elif dry_run:  # a dry run leaves nothing behind in the repository
        runs_dir = Path(tempfile.mkdtemp(prefix="portolan-eval-dry-"))
    else:
        runs_dir = config_module.repo_root() / "eval" / "runs"
    checkpoint = Checkpoint(runs_dir / ("dry-run.jsonl" if dry_run else "results.jsonl"))
    if fresh or dry_run:
        checkpoint.reset()

    config = load_config()
    db = open_database(config.storage.db_path) if not dry_run else None
    try:
        engine: Engine
        client: PoliteClient | None = None
        if dry_run:
            engine = FakeEngine()
            used_today = 0
        else:
            assert db is not None
            engine, client, used_today = _real_engine(config, db)
        done = {k for k, v in checkpoint.load().items() if v.status == "done"}
        todo = [r for r in sampled if (r.id, engine.version) not in done]
        est = estimate(
            0 if dry_run else len(todo), daily_limit=config.llm.limits.rpd, used_today=used_today
        )
        typer.echo(
            f"Sampled {len(sampled)} of {len(loaded.records)} record(s) "
            f"(seed {seed}, split {split})."
        )
        typer.echo(
            "Dry run: the fake engine makes no requests, and its numbers are not results."
            if dry_run
            else describe(est)
        )
        confirm_needed = not dry_run and bool(todo) and needs_confirmation(est) and not yes
        if confirm_needed and not typer.confirm("Spend these requests?", default=False):
            raise typer.Exit(code=1)
        summary = asyncio.run(_run(engine, client, sampled, checkpoint, retry_failed))
    finally:
        if db is not None:
            db.close()

    results = results_for(sampled, checkpoint, engine.version)
    matcher = DefaultMatcher()
    scores = score_records(sampled, results, matcher)
    n_done = sum(r.status == "done" for r in results.values())
    n_failed = sum(r.status == "failed" for r in results.values())
    meta = ReportMeta(
        command=(
            f"portolan eval --data {data_path} --limit {limit} --split {split} --seed {seed}"
            + (" --dry-run" if dry_run else "")
        ),
        date=date.today().isoformat(),
        seed=seed,
        limit=limit,
        split=split,
        n_sampled=len(sampled),
        n_done=n_done,
        n_failed=n_failed,
        n_pending=len(sampled) - n_done - n_failed,
        engine_version=engine.version,
        dataset_path=str(data_path),
        matcher_name=matcher.name,
        matcher_description=matcher.description,
        test_run=dry_run,
        requests_used=sum(r.requests_used for r in results.values()),
        notes=[f"{r.record_id}: {r.note}" for r in results.values() if r.note and not dry_run],
    )
    typer.echo(
        f"\nRan {summary.ran}, failed {summary.failed}, already done {summary.already_done}."
    )
    if summary.stopped_for_budget:
        typer.echo(
            f"Stopped: today's free-tier requests are used up. {summary.pending} record(s) "
            "are left. Progress is saved; run the same command again after midnight US Pacific "
            "time."
        )
    if n_done == 0:
        typer.echo("No record finished, so there is nothing to score or report.")
        raise typer.Exit(code=1 if not dry_run else 0)
    text = render_report(meta, sampled, results, scores)
    if dry_run:
        typer.echo("\n" + text)
        return
    path = write_report(
        Path(report_dir) if report_dir else config_module.repo_root() / "eval" / "reports",
        text,
        test_run=False,
    )
    typer.echo(f"Report written to {path}")


async def _run(
    engine: Engine,
    client: PoliteClient | None,
    sampled: list[EvalRecord],
    checkpoint: Checkpoint,
    retry_failed: bool,
) -> RunSummary:
    try:
        return await run(
            sampled, engine, checkpoint, retry_failed=retry_failed, progress=typer.echo
        )
    finally:
        if client is not None:
            await client.aclose()


def _real_engine(config: Config, db: Database) -> tuple[RealEngine, PoliteClient, int]:
    if config.llm.provider == "mock":
        typer.echo(
            "Evaluation needs the real provider: the mock provider has no recorded answers for "
            "these papers. Set PORTOLAN_LLM_PROVIDER=gemini (and GEMINI_API_KEY in .env)."
        )
        raise typer.Exit(code=1)
    if not api_key_present(config.llm.provider):
        typer.echo("GEMINI_API_KEY isn't set in .env. Add it there (never paste it into chat).")
        raise typer.Exit(code=1)
    if config.llm.models.placeholders():
        typer.echo("portolan.toml still has placeholder model ids. Run `portolan doctor` first.")
        raise typer.Exit(code=1)
    gateway = Gateway(config, db)
    client = PoliteClient(db, contact=config.http.contact)
    used = sum(int(r["requests"]) for r in gateway.limiter.budget.today())
    return RealEngine(config, db, gateway, client), client, used
