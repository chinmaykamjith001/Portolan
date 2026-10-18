from __future__ import annotations

import json
from pathlib import Path

import pytest

from portolan.evalharness.adapters import (
    DatasetError,
    inspect_dataset,
    load_records,
    resolve_fields,
)
from portolan.evalharness.records import record_id
from portolan.evalharness.sampling import sample_records
from tests.eval_helpers import gold, native_rows, record, write_jsonl


def test_native_rows_are_grouped_into_records_by_paper_repo_and_split(tmp_path: Path) -> None:
    rows = native_rows(2)
    rows.append({**rows[0], "id": "d0b", "description": "A second thing differs here."})
    rows.append({**rows[0], "id": "d0c", "split": "synthetic", "description": "Synthetic one."})
    result = load_records(write_jsonl(tmp_path / "d.jsonl", rows))
    sizes = sorted((r.split, len(r.gold)) for r in result.records)
    assert sizes == [("real", 1), ("real", 2), ("synthetic", 1)]
    assert result.rows == 4 and result.skipped == []


def test_other_field_names_are_mapped_through_aliases(tmp_path: Path) -> None:
    data = {
        "data": [
            {
                "Paper ID": "2106.09685",
                "GitHub URL": "https://github.com/microsoft/LoRA",
                "Discrepancy": "Alpha is scaled by r in the paper but not in the code.",
                "File": "loralib/layers.py",
                "Lines": "104-110",
                "Source": "Real-world",
                "Type": "algorithm_step",
            }
        ]
    }
    path = tmp_path / "x.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    [rec] = load_records(path).records
    [g] = rec.gold
    assert rec.paper == "2106.09685" and rec.split == "real"
    assert (g.code_file, g.code_start, g.code_end) == ("loralib/layers.py", 104, 110)
    assert g.category == "algorithm_step"


def test_csv_and_directory_loading_ignore_unrelated_files(tmp_path: Path) -> None:
    (tmp_path / "rows.csv").write_text(
        "paper,repo_url,description,split\n"
        "2001.00001,https://github.com/e/a,The loss differs from the paper,synthetic\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "z.json").write_text("[{}]", encoding="utf-8")
    result = load_records(tmp_path)
    assert [r.split for r in result.records] == ["synthetic"]
    assert len(result.files) == 1


def test_unmappable_rows_are_reported_not_dropped_silently(tmp_path: Path) -> None:
    rows = [*native_rows(1), {"id": "bad", "paper": "2001.1", "description": "No repository."}]
    result = load_records(write_jsonl(tmp_path / "d.jsonl", rows))
    assert len(result.records) == 1
    assert result.skipped == ["d.jsonl row 2: missing repo_url"]


def test_a_file_nothing_maps_to_points_at_inspect(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "d.jsonl", [{"foo": 1, "bar": 2}])
    with pytest.raises(DatasetError, match="--inspect"):
        load_records(path)


def test_missing_path_and_empty_folder_have_plain_messages(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="doesn't exist"):
        load_records(tmp_path / "nope")
    with pytest.raises(DatasetError, match="UKPLab/scicoqa"):
        load_records(tmp_path)


def test_record_ids_are_stable_and_input_order_does_not_matter(tmp_path: Path) -> None:
    rows = native_rows(4)
    a = load_records(write_jsonl(tmp_path / "a.jsonl", rows)).records
    b = load_records(write_jsonl(tmp_path / "b.jsonl", list(reversed(rows)))).records
    assert [r.id for r in a] == [r.id for r in b]
    assert record_id("p", "u", None, "real") != record_id("p", "u", None, "synthetic")
    assert record_id("p", "u/", None, "real") == record_id("p", "u", None, "real")


def test_inspect_lists_detected_and_missing_fields(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "d.jsonl",
        [{"paper": "2001.1", "repo": "https://github.com/e/r", "issue": "Loss differs"}],
    )
    text = inspect_dataset(path)
    assert "UNVERIFIED" in text
    assert "detected   paper" in text and "<- repo" in text
    assert "not found  code_file" in text
    assert "rows that can't be mapped: 0 of 1" in text


def test_resolve_fields_normalizes_key_spelling() -> None:
    fields = resolve_fields(["Paper-ID", "REPO URL", "Description"])
    assert fields == {"paper": "Paper-ID", "repo_url": "REPO URL", "description": "Description"}


def test_sampling_is_deterministic_filtered_and_order_independent() -> None:
    recs = [
        record(f"r{i:02d}", [gold("g", "x")], split="real" if i % 2 else "synthetic")
        for i in range(20)
    ]
    a = sample_records(recs, limit=5, seed=3)
    b = sample_records(list(reversed(recs)), limit=5, seed=3)
    assert [r.id for r in a] == [r.id for r in b] and len(a) == 5
    assert a == sorted(a, key=lambda r: r.id)
    assert any(
        [r.id for r in sample_records(recs, limit=5, seed=s)] != [r.id for r in a]
        for s in range(10)
    )
    real = sample_records(recs, limit=50, seed=0, split="real")
    assert len(real) == 10 and all(r.split == "real" for r in real)
    assert len(sample_records(recs, limit=0, seed=0)) == 20
