"""Shared builders for the evaluation harness tests."""

from __future__ import annotations

import json
from pathlib import Path

from portolan.evalharness.records import Discrepancy, EvalRecord
from portolan.models.code import CodeSpan
from portolan.models.finding import Finding


def finding(
    fid: str,
    *,
    verdict: str = "mismatch",
    claim: str = "A claim.",
    evidence: str = "Some evidence.",
    path: str | None = None,
    lines: tuple[int, int] = (1, 1),
    method: str = "llm_judge",
    claim_type: str = "hyperparameter",
) -> Finding:
    spans = (
        [CodeSpan(repo_id="r", commit_sha="c", path=path, start_line=lines[0], end_line=lines[1])]
        if path
        else []
    )
    return Finding.model_validate(
        {
            "id": fid,
            "claim_text": claim,
            "claim_type": claim_type,
            "paper_anchor": "sec:1",
            "code_spans": [s.model_dump() for s in spans],
            "verdict": verdict,
            "severity": "medium",
            "confidence": 0.8,
            "method": method,
            "evidence": evidence,
        }
    )


def gold(
    gid: str,
    description: str,
    *,
    file: str | None = None,
    lines: tuple[int, int] | None = None,
    category: str | None = None,
    split: str = "real",
) -> Discrepancy:
    return Discrepancy.model_validate(
        {
            "id": gid,
            "description": description,
            "code_file": file,
            "code_start": lines[0] if lines else None,
            "code_end": lines[1] if lines else None,
            "category": category,
            "split": split,
        }
    )


def record(rid: str, golds: list[Discrepancy], split: str = "real") -> EvalRecord:
    return EvalRecord.model_validate(
        {
            "id": rid,
            "paper": f"paper-{rid}",
            "repo_url": f"https://github.com/example/{rid}",
            "split": split,
            "gold": [g.model_dump() for g in golds],
        }
    )


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def native_rows(n: int, split: str = "real") -> list[dict[str, object]]:
    return [
        {
            "id": f"d{i}",
            "paper": f"2001.{i:05d}",
            "repo_url": f"https://github.com/example/repo{i}",
            "description": f"The paper uses setting number {i} but the code differs from it.",
            "code_file": "src/model.py",
            "code_start": 10 + i,
            "code_end": 12 + i,
            "split": split,
            "category": "hyperparameter",
        }
        for i in range(n)
    ]
