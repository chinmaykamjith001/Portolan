from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from portolan.models.repo import RepoStatus
from portolan.repo.upload import UploadError, extract_zip, safe_dirname, use_folder
from portolan.store.db import Database
from portolan.store.repos import choose_repo, chosen_repo, load_candidates, save_candidates


def make_zip(files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


def test_zip_extracts_into_workspace_and_strips_top_folder(tmp_path: Path) -> None:
    data = make_zip(
        {
            "toy-main/model.py": b"x = 1\n",
            "toy-main/pkg/train.py": b"y = 2\n",
            "toy-main/README.md": b"# toy\n",
            "toy-main/.git/config": b"[core]\n",
            "toy-main/__MACOSX/._model.py": b"junk",
        }
    )
    cand = extract_zip(data, "arxiv:2101.00001v1", tmp_path, "toy-main.zip")
    root = Path(cand.local_path or "")
    assert root.parent == tmp_path / "arxiv_2101.00001v1"
    assert (root / "model.py").read_bytes() == b"x = 1\n"
    assert (root / "pkg" / "train.py").is_file()
    assert not (root / ".git").exists() and not (root / "__MACOSX").exists()
    assert (cand.source, cand.host, cand.status, cand.name) == ("user", "upload", "ok", "toy-main")
    assert cand.id.startswith("upload:")


def test_zip_slip_and_symlinks_are_skipped(tmp_path: Path) -> None:
    data = make_zip(
        {"../evil.py": b"bad", "/abs.py": b"bad", "ok.py": b"good", "C:/win.py": b"bad"},
        symlink="link.py",
    )
    cand = extract_zip(data, "p", tmp_path)
    root = Path(cand.local_path or "")
    assert sorted(p.name for p in root.rglob("*")) == ["ok.py"]
    assert not (tmp_path / "evil.py").exists() and not (tmp_path.parent / "evil.py").exists()


def test_zip_bomb_and_bad_zip_are_refused(tmp_path: Path) -> None:
    bomb = make_zip({"big.py": b"0" * 5_000_000})
    with pytest.raises(UploadError, match="zip bomb"):
        extract_zip(bomb, "p", tmp_path)
    with pytest.raises(UploadError, match="isn't a valid zip"):
        extract_zip(b"not a zip", "p", tmp_path, "code.zip")
    with pytest.raises(UploadError, match="no files"):
        extract_zip(make_zip({"../only-evil.py": b"x"}), "p", tmp_path)


def test_zip_without_code_is_a_placeholder(tmp_path: Path) -> None:
    cand = extract_zip(make_zip({"README.md": b"soon"}), "p", tmp_path)
    assert cand.status == "placeholder" and cand.status_reason == "no source files in the upload"


def test_local_folder_is_used_in_place(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "net.py").write_text("pass\n")
    cand = use_folder(tmp_path)
    assert (
        cand.local_path == str(tmp_path.resolve()) and cand.host == "local" and cand.status == "ok"
    )
    with pytest.raises(UploadError, match="isn't a folder"):
        use_folder(tmp_path / "missing")


def test_safe_dirname() -> None:
    assert safe_dirname("arxiv:hep-th/9901001v2") == "arxiv_hep-th_9901001v2"


def test_store_candidates_and_choice(db: Database, tmp_path: Path) -> None:
    cand = use_folder(tmp_path)
    status = RepoStatus(kind="none", message="No code found for this paper")
    save_candidates(db, "p", [cand], status)
    loaded = load_candidates(db, "p")
    assert loaded == ([cand], status)
    assert load_candidates(db, "other") is None
    assert chosen_repo(db, "p") is None
    choose_repo(db, "p", cand)
    assert chosen_repo(db, "p") == cand
