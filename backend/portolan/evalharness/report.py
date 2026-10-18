"""Markdown report. Every number in it comes from the scored results passed in; a dry run (fake
engine) is stamped "TEST RUN — not a result" and can't be written to eval/reports/."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

from portolan.evalharness.checkpoint import RecordResult
from portolan.evalharness.records import Discrepancy, EvalRecord
from portolan.evalharness.scoring import Scores, Tally
from portolan.models.finding import Finding

EXAMPLES = 5
TEST_BANNER = (
    "> **TEST RUN — not a result.** This used the built-in fake engine on a sample dataset to "
    "check the harness. The numbers below say nothing about Portolan."
)


@dataclass
class ReportMeta:
    command: str
    date: str
    seed: int
    limit: int
    split: str
    n_sampled: int
    n_done: int
    n_failed: int
    n_pending: int
    engine_version: str
    dataset_path: str
    matcher_name: str
    matcher_description: str
    test_run: bool = False
    requests_used: int = 0
    notes: list[str] = field(default_factory=list[str])


def pct(t: Tally) -> str:
    return (
        f"{t.rate * 100:.1f}% ({t.hit}/{t.total})"
        if t.rate is not None
        else f"n/a ({t.hit}/{t.total})"
    )


def _cell(text: str, limit: int = 140) -> str:
    one = " ".join(text.split()).replace("|", "\\|")
    return one if len(one) <= limit else one[: limit - 1] + "…"


def _table(header: tuple[str, str], rows: dict[str, Tally]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    out = [f"| {header[0]} | {header[1]} |", "|---|---|"]
    out += [f"| {k} | {pct(v)} |" for k, v in rows.items()]
    return [*out, ""]


def render_report(
    meta: ReportMeta,
    records: list[EvalRecord],
    results: dict[str, RecordResult],
    scores: Scores,
) -> str:
    by_rec = {r.id: r for r in records}
    lines: list[str] = []
    if meta.test_run:
        lines += [TEST_BANNER, ""]
    lines += [f"# SciCoQA evaluation, {meta.date}", ""]
    if meta.n_pending or meta.n_failed:
        lines += [
            f"> **Partial.** {meta.n_done} of {meta.n_sampled} sampled records finished"
            f" ({meta.n_failed} failed, {meta.n_pending} not run)."
            " Numbers cover finished records only.",
            "",
        ]
    lines += [
        "> The SciCoQA file format was read through an UNVERIFIED adapter "
        "(`portolan/evalharness/adapters.py`) and the matcher below is Portolan's own, "
        "not the benchmark's. Treat the numbers accordingly (see eval/README.md).",
        "",
        "## Setup",
        "",
        f"- Command: `{meta.command}`",
        f"- Dataset: `{meta.dataset_path}`",
        f"- Sample: {meta.n_sampled} record(s), limit {meta.limit}, split `{meta.split}`, "
        f"seed {meta.seed}",
        f"- Engine version: `{meta.engine_version}`",
        f"- Matcher `{meta.matcher_name}`: {meta.matcher_description}",
        f"- Real requests used (measured): {meta.requests_used}",
        *[f"- {n}" for n in meta.notes],
        "",
        "## Overall",
        "",
        f"- Recall: **{pct(scores.gold)}** of gold discrepancies were found",
        f"- Precision: **{pct(scores.predictions)}** of predicted mismatches matched a listed "
        "discrepancy (a lower bound if the gold list is incomplete)",
        f"- Low-confidence (`uncertain`) findings, not counted as predictions: {scores.uncertain}",
        "",
        "## Recall by split",
        "",
        *_table(("Split", "Recall"), scores.recall_by_split),
        "## Recall by dataset category",
        "",
        *_table(("Category", "Recall"), scores.recall_by_category),
        "## Precision by claim type (the engine's label)",
        "",
        *_table(("Claim type", "Precision"), scores.precision_by_claim_type),
        "## What decided the matched findings",
        "",
    ]
    if scores.matched_by_method:
        lines += ["| Method | Matched findings |", "|---|---|"]
        lines += [f"| {m} | {n} |" for m, n in sorted(scores.matched_by_method.items())]
        lines.append("")
    else:
        lines += ["_no matches_", ""]

    hits: list[str] = []
    misses: list[str] = []
    for rs in scores.records:
        rec, res = by_rec[rs.record_id], results[rs.record_id]
        findings = {f.id: f for f in res.findings}
        gold = {g.id: g for g in rec.gold}
        for m in rs.matches:
            if len(hits) < EXAMPLES:
                hits.append(_hit(rec, gold[m.gold_id], findings[m.finding_id]))
        for gid in rs.missed:
            if len(misses) < EXAMPLES:
                misses.append(_miss(rec, gold[gid]))
    lines += ["## Examples: found", "", *(hits or ["_none_"]), "", "## Examples: missed", ""]
    lines += [*(misses or ["_none_"]), ""]

    failed = [r for r in results.values() if r.status == "failed" and r.record_id in by_rec]
    if failed:
        lines += ["## Records that failed", "", "| Record | Reason |", "|---|---|"]
        lines += [f"| {r.record_id} | {_cell(r.error or 'unknown')} |" for r in failed]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _hit(rec: EvalRecord, gold: Discrepancy, f: Finding) -> str:
    where = f" (`{gold.code_file}`)" if gold.code_file else ""
    return (
        f"- **{rec.paper}**{where}: gold “{_cell(gold.description)}” ← "
        f"{f.method} finding “{_cell(f.claim_text)}” (confidence {f.confidence:.2f})"
    )


def _miss(rec: EvalRecord, gold: Discrepancy) -> str:
    where = f" (`{gold.code_file}`)" if gold.code_file else ""
    return f"- **{rec.paper}**{where}: “{_cell(gold.description)}”"


def write_report(reports_dir: Path, text: str, *, day: str | None = None, test_run: bool) -> Path:
    """eval/reports/<date>.md (a numeric suffix if one exists). Test runs are refused."""
    if test_run:
        raise ValueError("A test run is not a result and is never written to the reports folder.")
    reports_dir.mkdir(parents=True, exist_ok=True)
    day = day or Date.today().isoformat()
    path = reports_dir / f"{day}.md"
    n = 2
    while path.exists():
        path = reports_dir / f"{day}-{n}.md"
        n += 1
    path.write_text(text, encoding="utf-8")
    return path
