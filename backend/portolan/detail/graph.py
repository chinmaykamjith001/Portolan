"""Graph templates whose data comes from the sandbox (spec §10.7.4).

The model picks a template (or "none") and writes a program; the data below is what the program
printed, validated and normalised here. The frontend renders `GraphSpec.data` as-is, so every key
listed is always present (optional ones default as noted) and every number is finite.

`GraphSpec.data` per template. Strings are at most 40 characters, numbers have at most six
significant digits.

bar
    {"x": [str, ...],                       # 1-60 category labels
     "series": [{"name": str, "y": [num, ...]}, ...],   # 1-6 series, len(y) == len(x)
     "x_label": str, "y_label": str}        # "" when absent

line
    {"x": [num, ...],                       # 2-200 numeric x values, ascending
     "series": [{"name": str, "y": [num, ...]}, ...],   # 1-6 series, len(y) == len(x)
     "x_label": str, "y_label": str}

heatmap
    {"rows": [str, ...],                    # 1-24 row labels (top to bottom)
     "cols": [str, ...],                    # 1-24 column labels (left to right)
     "values": [[num, ...], ...],           # len(rows) lists of len(cols) numbers
     "x_label": str, "y_label": str}        # x_label names the columns, y_label the rows

before_after
    {"labels": [str, ...],                  # 1-20 categories
     "before": [num, ...], "after": [num, ...],         # len == len(labels)
     "before_name": str, "after_name": str,             # "Before" / "After" when absent
     "x_label": str, "y_label": str}

distribution
    {"x": [num, ...],                       # 2-200 points: bin centres or curve x values
     "y": [num, ...],                       # len(y) == len(x): counts or densities
     "style": "bars" | "curve",             # bars = histogram, curve = a function
     "bin_width": num | null,               # set when style == "bars"
     "x_label": str, "y_label": str}
    The program may print {"values": [...], "bins": int} (raw samples; binned here, 2-5,000
    values, 5-40 bins, style "bars") or {"x": [...], "y": [...]} (style "curve" unless it also
    prints "style": "bars").

`make_graph` returns None when the model chose "none" (the drawer then hides the Graph tab), when
the program failed twice, or when its output didn't fit the shape. Gateway errors (rate limits,
a down provider) are not swallowed: the job fails and can be retried, instead of caching "none".
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

from portolan.detail.explain import (
    DetailError,
    _passages,  # pyright: ignore[reportPrivateUsage]
    default_path,
    path_titles,
)
from portolan.detail.grounded_text import NumberGuard
from portolan.llm.gateway import Gateway
from portolan.llm.prompts import graph_program
from portolan.llm.prompts.program_schemas import GraphOut
from portolan.models.detail import GraphSpec
from portolan.models.paper import PaperDoc
from portolan.sandbox import run_program
from portolan.store.chart import ChartStore

ATTEMPTS = 2
MAX_LABEL = 40
MAX_SERIES = 6
MAX_BARS = 60
MAX_LINE_POINTS = 200
MAX_HEATMAP_SIDE = 24
MAX_BEFORE_AFTER = 20
MAX_SAMPLES = 5000
MAX_PROGRAM_LINES = 60


class _Invalid(Exception):
    """The output doesn't fit the template's shape; the message goes back to the model."""


def _num(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Invalid("Data values must be numbers.")
    if not math.isfinite(value) or abs(value) > 1e12:
        raise _Invalid("Data values must be finite numbers below 1e12.")
    return float(f"{value:.6g}")


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, float):
        value = f"{value:.6g}"
    return str(value)[:MAX_LABEL]


def _list(value: object, name: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:  # type: ignore[reportUnknownArgumentType]
        raise _Invalid(f'"{name}" must be a list of {low} to {high} items.')
    return value  # type: ignore[reportUnknownVariableType]


def _numbers(value: object, name: str, count: int | None, low: int, high: int) -> list[float]:
    items = _list(value, name, low, high)
    if count is not None and len(items) != count:
        raise _Invalid(f'"{name}" must have {count} items, one per label.')
    return [_num(v) for v in items]


def _axes(data: dict[str, Any]) -> dict[str, str]:
    return {"x_label": _text(data.get("x_label")), "y_label": _text(data.get("y_label"))}


def _xy_series(data: dict[str, Any], template: str) -> dict[str, object]:
    is_line = template == "line"
    raw_x = _list(data.get("x"), "x", 2 if is_line else 1, MAX_LINE_POINTS if is_line else MAX_BARS)
    x: list[float] | list[str]
    if is_line:
        x = [_num(v) for v in raw_x]
        if any(b < a for a, b in pairwise(x)):
            raise _Invalid('"x" must be ascending for a line graph.')
    else:
        x = [_text(v) for v in raw_x]
    series: list[dict[str, object]] = []
    for i, s in enumerate(_list(data.get("series"), "series", 1, MAX_SERIES)):
        if not isinstance(s, dict):
            raise _Invalid('Each series must be {"name": str, "y": [numbers]}.')
        series.append(
            {
                "name": _text(s.get("name"), f"Series {i + 1}"),  # type: ignore[reportUnknownMemberType]
                "y": _numbers(s.get("y"), "y", len(raw_x), 1, len(raw_x)),  # type: ignore[reportUnknownMemberType]
            }
        )
    return {"x": x, "series": series, **_axes(data)}


def _heatmap(data: dict[str, Any]) -> dict[str, object]:
    rows = [_text(r) for r in _list(data.get("rows"), "rows", 1, MAX_HEATMAP_SIDE)]
    cols = [_text(c) for c in _list(data.get("cols"), "cols", 1, MAX_HEATMAP_SIDE)]
    raw = _list(data.get("values"), "values", len(rows), len(rows))
    values = [_numbers(r, "values row", len(cols), len(cols), len(cols)) for r in raw]
    return {"rows": rows, "cols": cols, "values": values, **_axes(data)}


def _before_after(data: dict[str, Any]) -> dict[str, object]:
    labels = [_text(v) for v in _list(data.get("labels"), "labels", 1, MAX_BEFORE_AFTER)]
    n = len(labels)
    return {
        "labels": labels,
        "before": _numbers(data.get("before"), "before", n, n, n),
        "after": _numbers(data.get("after"), "after", n, n, n),
        "before_name": _text(data.get("before_name"), "Before"),
        "after_name": _text(data.get("after_name"), "After"),
        **_axes(data),
    }


def _histogram(values: list[float], bins: object) -> tuple[list[float], list[float], float]:
    lo, hi = min(values), max(values)
    if lo == hi:
        raise _Invalid("All sampled values are equal; there is no distribution to show.")
    count = (
        bins
        if isinstance(bins, int) and not isinstance(bins, bool)
        else round(math.sqrt(len(values)))
    )
    count = max(5, min(40, count))
    width = (hi - lo) / count
    counts = [0] * count
    for v in values:
        counts[min(int((v - lo) / width), count - 1)] += 1
    centres = [float(f"{lo + width * (i + 0.5):.6g}") for i in range(count)]
    return centres, [float(c) for c in counts], float(f"{width:.6g}")


def _distribution(data: dict[str, Any]) -> dict[str, object]:
    if "values" in data:
        values = _numbers(data.get("values"), "values", None, 2, MAX_SAMPLES)
        x, y, width = _histogram(values, data.get("bins"))
        return {"x": x, "y": y, "style": "bars", "bin_width": width, **_axes(data)}
    x = _numbers(data.get("x"), "x", None, 2, MAX_LINE_POINTS)
    y = _numbers(data.get("y"), "y", len(x), len(x), len(x))
    if data.get("style") == "bars":
        width = float(f"{min(b - a for a, b in pairwise(x)):.6g}")
        if width <= 0:
            raise _Invalid('"x" must be strictly ascending for bars.')
        return {"x": x, "y": y, "style": "bars", "bin_width": width, **_axes(data)}
    return {"x": x, "y": y, "style": "curve", "bin_width": None, **_axes(data)}


def normalise(template: str, output: object) -> dict[str, object]:
    """Validate the program's printed JSON against the template's shape (see module docstring)."""
    if not isinstance(output, dict):
        raise _Invalid("Print one JSON object in the template's shape.")
    data: dict[str, Any] = output  # type: ignore[reportUnknownVariableType]
    if template in ("bar", "line"):
        return _xy_series(data, template)
    if template == "heatmap":
        return _heatmap(data)
    if template == "before_after":
        return _before_after(data)
    if template == "distribution":
        return _distribution(data)
    raise _Invalid(f"Unknown template {template!r}.")


def _numbers_in_data(value: object) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers_in_data(v)]  # type: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers_in_data(v)]  # type: ignore[reportUnknownVariableType]
    return []


def _text_in_data(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for v in value for t in _text_in_data(v)]  # type: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _text_in_data(v)]  # type: ignore[reportUnknownVariableType]
    return []


async def make_graph(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, concept_id: str
) -> GraphSpec | None:
    """Pick a template and compute its data in the sandbox; `None` when no visual would help."""
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
    }
    retry: dict[str, str] | None = None
    for _ in range(ATTEMPTS):
        inputs = {**base, "retry": retry} if retry else dict(base)
        result = await gateway.call(
            "default", graph_program.TEMPLATE, inputs, GraphOut, "P1", doc.paper_id
        )
        out: GraphOut = result.parsed
        if out.template == "none":
            return None
        problem: str | None = None
        data: dict[str, object] = {}
        if not out.program.strip():
            problem = "The program is empty."
        elif len(out.program.strip().splitlines()) > MAX_PROGRAM_LINES:
            problem = (
                f"The program is too long; keep it to 40 lines (not over {MAX_PROGRAM_LINES})."
            )
        else:
            run = await run_program(out.program)
            if not run.ok:
                problem = run.error or "The program failed to run."
            else:
                try:
                    data = normalise(out.template, run.output)
                except _Invalid as exc:
                    problem = str(exc)
        if problem is not None:
            retry = {"program": out.program, "error": problem}
            continue
        guard = NumberGuard([out.program, *_text_in_data(data)], _numbers_in_data(data))
        return GraphSpec(
            concept_id=concept_id,
            template=out.template,
            caption=guard.check(out.caption) or concept.title,
            data=data,
            note=guard.check(out.note),
        )
    return None
