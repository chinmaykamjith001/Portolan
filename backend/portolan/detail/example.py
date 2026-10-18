"""Worked examples whose numbers come from the sandbox (spec §10.7.3)."""

from __future__ import annotations

import math
from typing import Any

from portolan.detail.explain import (
    DetailError,
    _passages,  # pyright: ignore[reportPrivateUsage]
    default_path,
    path_titles,
)
from portolan.detail.grounded_text import NumberGuard
from portolan.llm.gateway import Gateway
from portolan.llm.prompts import example_program
from portolan.llm.prompts.program_schemas import ExampleOut
from portolan.models.detail import Example
from portolan.models.paper import PaperDoc
from portolan.sandbox import run_program
from portolan.store.chart import ChartStore

MAX_ROWS = 12
MAX_COLS = 8
MAX_CELL_CHARS = 40
MAX_PROGRAM_LINES = 60  # the prompt asks for 40; this is the cutoff for "obviously not a snippet"
ATTEMPTS = 2  # the first try, then one retry with the problem appended


class _Invalid(Exception):
    """The program ran, but its output isn't a usable table. The message goes back to the model."""


def _cell(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Invalid("A cell is NaN or infinite; show a finite value or the word 'overflow'.")
        return f"{value:.6g}"
    if isinstance(value, (int, str)):
        return str(value)[:MAX_CELL_CHARS]
    raise _Invalid("Cells must be strings or numbers, not lists or objects.")


def table_from_output(output: object) -> tuple[list[str], list[list[str]]]:
    """Validate the program's printed JSON and turn every cell into a string."""
    if not isinstance(output, dict):
        raise _Invalid('Print one JSON object: {"columns": [...], "rows": [[...], ...]}.')
    columns, rows = output.get("columns"), output.get("rows")  # type: ignore[reportUnknownMemberType]
    if not isinstance(columns, list) or not isinstance(rows, list) or not columns or not rows:
        raise _Invalid('The JSON needs a non-empty "columns" list and a non-empty "rows" list.')
    cols = [_cell(c) for c in columns[:MAX_COLS]]  # type: ignore[reportUnknownVariableType]
    out: list[list[str]] = []
    for row in rows[:MAX_ROWS]:  # type: ignore[reportUnknownVariableType]
        if not isinstance(row, list) or len(row) < len(cols):  # type: ignore[reportUnknownArgumentType]
            raise _Invalid("Every row must be a list with one cell per column.")
        out.append([_cell(c) for c in row[: len(cols)]])  # type: ignore[reportUnknownVariableType]
    return cols, out


def program_problem(program: str) -> str | None:
    if not program.strip():
        return "The program is empty."
    if len(program.strip().splitlines()) > MAX_PROGRAM_LINES:
        return (
            f"The program is too long; keep it to 40 lines (it has more than {MAX_PROGRAM_LINES})."
        )
    return None


async def make_example(
    doc: PaperDoc,
    gateway: Gateway,
    chart: ChartStore,
    concept_id: str,
    variant: int,
    previous: list[Example],
) -> Example:
    """Ask for a program, run it in the sandbox, keep only executed output.

    `variant` > 0 asks for a different case (preferably an edge case) than `previous`.
    """
    concept = chart.get_concept(doc.paper_id, concept_id)
    if concept is None:
        raise DetailError("That concept isn't on this paper's map.")
    titles = path_titles(chart, doc.paper_id, default_path(chart, doc.paper_id, concept))
    base: dict[str, Any] = {
        "paper_title": doc.title,
        "concept": {"title": concept.title, "gist": concept.gist},
        "path": titles or [concept.title],
        "passages": _passages(doc, concept.anchors),
        "symbols": doc.symbols,
        "variant": variant,
        "previous": [
            {"caption": e.caption, "program": e.program, "columns": e.columns, "rows": e.rows}
            for e in previous
        ],
    }
    retry: dict[str, str] | None = None
    for _ in range(ATTEMPTS):
        inputs = {**base, "retry": retry} if retry else dict(base)
        result = await gateway.call(
            "default", example_program.TEMPLATE, inputs, ExampleOut, "P0", doc.paper_id
        )
        out: ExampleOut = result.parsed
        problem = program_problem(out.program)
        columns: list[str] = []
        rows: list[list[str]] = []
        if problem is None:
            run = await run_program(out.program)
            if run.ok:
                try:
                    columns, rows = table_from_output(run.output)
                except _Invalid as exc:
                    problem = str(exc)
            else:
                problem = run.error or "The program failed to run."
        if problem is not None:
            retry = {"program": out.program, "error": problem}
            continue
        guard = NumberGuard([out.program, *columns, *(c for r in rows for c in r)])
        return Example(
            concept_id=concept_id,
            variant=variant,
            source_label=out.source_label,
            caption=guard.check(out.caption) or f"Worked example: {concept.title}",
            program=out.program,
            columns=columns,
            rows=rows,
            note=guard.check(out.note),
        )
    raise DetailError("Couldn't build a worked example for this concept. Try again.")
