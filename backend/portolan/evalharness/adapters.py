"""Read a SciCoQA download into `EvalRecord`s. THE ONLY MODULE THAT KNOWS THE FILE FORMAT.

The official format could not be inspected when this was written (no network access to
github.com/UKPLab/scicoqa), so this does not claim to match it. It reads JSON, JSONL and CSV
files, matches field names case-insensitively against `ALIASES`, and reports every row it cannot
map instead of dropping it silently. Run `portolan eval --inspect PATH` on the real download,
compare the "detected" and "not found" lines with the official repo's README, and fix `ALIASES`
(or add an adapter function) here.

Internal (flat, one row per discrepancy) schema, also accepted as-is:
    id, paper, repo_url, repo_commit, description, code_file, code_start, code_end,
    paper_location, split ("real"|"synthetic"), category
Rows sharing (paper, repo_url, repo_commit, split) are grouped into one `EvalRecord`.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from portolan.evalharness.records import Discrepancy, EvalRecord, Split, record_id

REQUIRED = ("paper", "repo_url", "description")

# internal field -> accepted source keys (normalized: lowercase, non-alphanumerics -> "_")
ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "uid", "discrepancy_id", "question_id", "qid", "example_id"),
    "paper": ("paper", "paper_id", "arxiv_id", "arxiv", "paper_url", "paper_link", "paper_title"),
    "repo_url": (
        "repo_url",
        "repo",
        "repository",
        "repository_url",
        "github_url",
        "code_url",
        "code_repo",
        "repo_link",
        "github",
    ),
    "repo_commit": ("repo_commit", "commit", "commit_sha", "commit_hash", "sha", "base_commit"),
    "description": (
        "description",
        "discrepancy",
        "discrepancy_description",
        "issue",
        "explanation",
        "summary",
        "text",
        "answer",
    ),
    "code_file": ("code_file", "file", "file_path", "filepath", "code_path", "path", "filename"),
    "code_start": ("code_start", "start_line", "line_start", "start", "line_from"),
    "code_end": ("code_end", "end_line", "line_end", "end", "line_to"),
    "code_lines": ("lines", "line_range", "code_lines", "line_numbers", "line"),
    "paper_location": (
        "paper_location",
        "paper_section",
        "section",
        "location_in_paper",
        "paper_loc",
    ),
    "split": ("split", "source", "origin", "kind", "dataset_split"),
    "category": ("category", "type", "claim_type", "discrepancy_type", "error_type", "label"),
}
DATA_SUFFIXES = {".json", ".jsonl", ".csv"}
SKIP_NAMES = {"package.json", "package-lock.json", "tsconfig.json", "composer.json"}
MAX_FILE_BYTES = 200_000_000


class DatasetError(Exception):
    """Plain-language problem reading the dataset."""


@dataclass
class LoadResult:
    records: list[EvalRecord] = field(default_factory=list[EvalRecord])
    files: list[str] = field(default_factory=list[str])
    rows: int = 0
    skipped: list[str] = field(default_factory=list[str])  # one line per unmappable row


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def data_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise DatasetError(f"{path} doesn't exist. Download SciCoQA and pass its folder as --data.")
    found = [
        p
        for p in sorted(path.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in DATA_SUFFIXES
        and p.name not in SKIP_NAMES
        and not any(part.startswith(".") for part in p.relative_to(path).parts)
        and p.stat().st_size <= MAX_FILE_BYTES
    ]
    if not found:
        raise DatasetError(
            f"No .json, .jsonl or .csv files under {path}. "
            "Clone https://github.com/UKPLab/scicoqa and point --data at it."
        )
    return found


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Every object in the file, whatever the container (list, JSONL, dict of rows, CSV)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open(newline="", encoding="utf-8") as fh:
                return [dict(r) for r in csv.DictReader(fh)]
        text = path.read_text(encoding="utf-8")
        if suffix == ".jsonl":
            return [_as_row(json.loads(line)) for line in text.splitlines() if line.strip()]
        return _rows_from_json(json.loads(text))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        raise DatasetError(f"Couldn't read {path.name}: {exc}") from None


def _as_row(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {"value": value}


def _rows_from_json(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [_as_row(v) for v in cast(list[Any], data)]
    if isinstance(data, dict):
        mapping = cast(dict[str, Any], data)
        values: list[Any] = list(mapping.values())
        lists = [cast(list[Any], v) for v in values if isinstance(v, list)]
        if len(lists) == 1:  # {"data": [...]}
            return [_as_row(v) for v in lists[0]]
        if values and all(isinstance(v, dict) for v in values):  # {"id1": {...}, "id2": {...}}
            return [{"id": k, **_as_row(v)} for k, v in mapping.items()]
        return [mapping]
    return [{"value": data}]


def resolve_fields(keys: list[str]) -> dict[str, str]:
    """internal field -> the source key that supplies it (first alias that is present)."""
    by_norm = {_norm(k): k for k in keys}
    out: dict[str, str] = {}
    for internal, aliases in ALIASES.items():
        for alias in aliases:
            if alias in by_norm:
                out[internal] = by_norm[alias]
                break
    return out


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items: list[Any] = list(cast(Any, value))
        return ", ".join(str(v) for v in items if v is not None) or None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _lines(value: Any) -> tuple[int | None, int | None]:
    """ "10-20", "10:20", [10, 20], 10 -> (start, end)."""
    if isinstance(value, (list, tuple)) and value:
        items: list[Any] = list(cast(Any, value))
        found = [n for n in (_int(v) for v in items) if n is not None]
        return (found[0], found[-1]) if found else (None, None)
    found_text: list[str] = re.findall(r"\d+", str(cast(Any, value))) if value is not None else []
    digits = [int(n) for n in found_text]
    return (digits[0], digits[-1]) if digits else (None, None)


def normalize_split(value: Any) -> Split:
    text = (_text(value) or "").lower()
    if "synth" in text:
        return "synthetic"
    if "real" in text:
        return "real"
    return "unknown"


def _row_to_gold(
    row: dict[str, Any], fields: dict[str, str], index: int
) -> tuple[Discrepancy, str, str, str | None] | str:
    """(gold, paper, repo_url, commit) or a plain reason the row can't be mapped."""

    def get(name: str) -> Any:
        key = fields.get(name)
        return row.get(key) if key is not None else None

    missing = [f for f in REQUIRED if f not in fields or _text(get(f)) is None]
    if missing:
        return "missing " + ", ".join(missing)
    start, end = _int(get("code_start")), _int(get("code_end"))
    if start is None and end is None and "code_lines" in fields:
        start, end = _lines(get("code_lines"))
    gold = Discrepancy(
        id=_text(get("id")) or f"row-{index}",
        description=_text(get("description")) or "",
        code_file=_text(get("code_file")),
        code_start=start,
        code_end=end if end is not None else start,
        paper_location=_text(get("paper_location")),
        category=_text(get("category")),
        split=normalize_split(get("split")),
    )
    return gold, _text(get("paper")) or "", _text(get("repo_url")) or "", _text(get("repo_commit"))


def load_records(path: Path) -> LoadResult:
    """Read every data file under `path`; group discrepancies into records."""
    result = LoadResult()
    groups: dict[tuple[str, str, str | None, Split], list[Discrepancy]] = {}
    for file in data_files(path):
        rows = read_rows(file)
        if not rows:
            continue
        fields = resolve_fields(sorted({k for r in rows for k in r}))
        used = 0
        for i, row in enumerate(rows):
            mapped = _row_to_gold(row, fields, i)
            if isinstance(mapped, str):
                if len(result.skipped) < 50:
                    result.skipped.append(f"{file.name} row {i + 1}: {mapped}")
                continue
            gold, paper, repo, commit = mapped
            groups.setdefault((paper, repo, commit, gold.split), []).append(gold)
            used += 1
        result.rows += len(rows)
        if used:
            result.files.append(str(file))
    for (paper, repo, commit, split), gold in groups.items():
        ids = {g.id for g in gold}
        if len(ids) != len(gold):  # keep ids unique inside a record
            gold = [g.model_copy(update={"id": f"{g.id}#{n}"}) for n, g in enumerate(gold)]
        result.records.append(
            EvalRecord(
                id=record_id(paper, repo, commit, split),
                paper=paper,
                repo_url=repo,
                repo_commit=commit,
                split=split,
                gold=gold,
            )
        )
    result.records.sort(key=lambda r: r.id)
    if not result.records:
        raise DatasetError(
            "Couldn't map any row to a paper, repository and description. "
            "Run `portolan eval --inspect PATH` to see the fields and adjust ALIASES in "
            "portolan/evalharness/adapters.py."
        )
    return result


def inspect_dataset(path: Path, samples: int = 3) -> str:
    """Human-readable view of the layout and field mapping, for adapting `ALIASES`."""
    lines = ["SciCoQA dataset inspection (the official format is UNVERIFIED; check this output).\n"]
    for file in data_files(path)[:20]:
        rows = read_rows(file)
        lines.append(f"{file}  ({file.suffix.lower()[1:]}, {len(rows)} row(s))")
        if not rows:
            continue
        keys = sorted({k for r in rows for k in r})
        fields = resolve_fields(keys)
        lines.append("  keys: " + ", ".join(keys))
        for internal in (
            *REQUIRED,
            "id",
            "repo_commit",
            "code_file",
            "code_start",
            "code_end",
            "code_lines",
            "paper_location",
            "split",
            "category",
        ):
            if internal in fields:
                lines.append(f"  detected   {internal:<15} <- {fields[internal]}")
            else:
                need = "REQUIRED, " if internal in REQUIRED else ""
                tried = ", ".join(ALIASES[internal][:4])
                lines.append(f"  not found  {internal:<15} ({need}tried: {tried}, ...)")
        for row in rows[:samples]:
            preview = {k: _preview(v) for k, v in list(row.items())[:12]}
            lines.append(f"  e.g. {json.dumps(preview, ensure_ascii=False)}")
        bad = sum(isinstance(_row_to_gold(r, fields, i), str) for i, r in enumerate(rows))
        lines.append(f"  rows that can't be mapped: {bad} of {len(rows)}\n")
    return "\n".join(lines)


def _preview(value: Any, limit: int = 60) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value
