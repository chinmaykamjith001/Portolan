"""Cloner tests. Local repos are cloned through file:// via the test-only
``allow_file_protocol`` and ``url_override`` arguments."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from portolan.models.repo import RepoCandidate
from portolan.repo.cloner import CloneError, clone

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *GIT_ID, "-c", "protocol.file.allow=always", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def candidate(url: str = "https://github.com/acme/widget.git", **kw: object) -> RepoCandidate:
    data: dict[str, object] = {
        "id": "github:acme/widget",
        "url": url,
        "owner": "acme",
        "name": "widget",
        "source": "user",
        "official": None,
        "official_evidence": None,
        "status": "ok",
    }
    data.update(kw)
    return RepoCandidate.model_validate(data)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    sub = tmp_path / "sub"
    sub.mkdir()
    git(sub, "init", "-q", "-b", "main")
    (sub / "secret.py").write_text("SECRET = 1\n")
    git(sub, "add", ".")
    git(sub, "commit", "-q", "-m", "sub")

    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "uploadpack.allowFilter", "true")
    git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    (src / "model.py").write_text("def f():\n    return 1\n")
    (src / "train.py").write_text("import model\n")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "one")
    (src / "big.bin").write_bytes(os.urandom(1_200_000))
    git(src, "submodule", "add", "-q", sub.as_uri(), "vendor/sub")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "two")
    return src


def do_clone(src: Path, tmp_path: Path, **kw: object):
    return clone(
        candidate(),
        tmp_path / "ws",
        "arXiv:2401.00001",
        allow_file_protocol=True,
        url_override=src.as_uri(),
        **kw,  # type: ignore[arg-type]
    )


def test_clone_is_shallow_with_sha_and_skips_big_file(source_repo: Path, tmp_path: Path) -> None:
    res = do_clone(source_repo, tmp_path)
    assert res.root == tmp_path / "ws" / "arXiv_2401.00001" / "acme__widget"
    assert res.commit_sha == git(source_repo, "rev-parse", "HEAD").strip()
    assert (res.root / ".git" / "shallow").exists()
    assert git(res.root, "rev-list", "--count", "HEAD").strip() == "1"
    assert (res.root / "model.py").read_text() == "def f():\n    return 1\n"
    assert not (res.root / "big.bin").exists()
    assert res.skipped_large == ["big.bin"]


def test_submodule_not_fetched(source_repo: Path, tmp_path: Path) -> None:
    res = do_clone(source_repo, tmp_path)
    assert (res.root / ".gitmodules").exists()
    sub = res.root / "vendor" / "sub"
    assert not (sub / "secret.py").exists()
    assert not sub.exists() or not any(sub.iterdir())


def test_hook_from_template_does_not_run(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "hook-ran"
    tpl = tmp_path / "tpl"
    (tpl / "hooks").mkdir(parents=True)
    hook = tpl / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\n")
    hook.chmod(0o755)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tpl))

    # Control: an unprotected clone with the same template runs the hook.
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "clone",
            "-q",
            source_repo.as_uri(),
            str(tmp_path / "ctl"),
        ],
        check=True,
        capture_output=True,
    )
    assert marker.exists()
    marker.unlink()

    res = do_clone(source_repo, tmp_path)
    assert res.root.is_dir()
    assert not marker.exists()
    assert not (res.root / ".git" / "hooks").exists()


def test_second_call_reuses_clone(source_repo: Path, tmp_path: Path) -> None:
    first = do_clone(source_repo, tmp_path)
    (first.root / "local-note.txt").write_text("kept")
    # Even with the source gone, the existing clone is reused.
    second = clone(
        candidate(),
        tmp_path / "ws",
        "arXiv:2401.00001",
        allow_file_protocol=True,
        url_override=(tmp_path / "nowhere").as_uri(),
    )
    assert second.root == first.root
    assert second.commit_sha == first.commit_sha
    assert second.skipped_large == ["big.bin"]
    assert (second.root / "local-note.txt").exists()


def test_missing_repo_gives_plain_error(tmp_path: Path) -> None:
    with pytest.raises(
        CloneError, match=r"Couldn't clone acme/widget: repository not found or private\."
    ):
        clone(
            candidate(),
            tmp_path / "ws",
            "p1",
            allow_file_protocol=True,
            url_override=(tmp_path / "nowhere").as_uri(),
        )
    assert not (tmp_path / "ws" / "p1" / "acme__widget").exists()


def test_non_https_url_refused(tmp_path: Path) -> None:
    with pytest.raises(CloneError, match="https"):
        clone(candidate("ssh://git@github.com/acme/widget.git"), tmp_path / "ws", "p1")
    with pytest.raises(CloneError, match="https"):
        clone(candidate("file:///etc"), tmp_path / "ws", "p1")


def test_upload_content_hash_is_stable(tmp_path: Path) -> None:
    folder = tmp_path / "up"
    (folder / "pkg").mkdir(parents=True)
    (folder / "pkg" / "a.py").write_text("x = 1\n")
    (folder / "big.dat").write_bytes(b"0" * 50)
    cand = candidate(host="upload", id="upload:abc", local_path=str(folder))

    a = clone(cand, tmp_path / "ws", "p1", max_file_bytes=10)
    b = clone(cand, tmp_path / "ws", "p1", max_file_bytes=10)
    assert a.root == folder
    assert a.commit_sha == b.commit_sha
    assert a.commit_sha.startswith("content:")
    assert a.skipped_large == ["big.dat"]

    (folder / "pkg" / "a.py").write_text("x = 22\n")
    c = clone(cand, tmp_path / "ws", "p1", max_file_bytes=10)
    assert c.commit_sha != a.commit_sha


def test_local_host_missing_folder(tmp_path: Path) -> None:
    cand = candidate(host="local", id="local:abc", local_path=str(tmp_path / "gone"))
    with pytest.raises(CloneError, match="no longer exists"):
        clone(cand, tmp_path / "ws", "p1")


@pytest.mark.network
def test_real_github_clone(tmp_path: Path) -> None:
    cand = candidate(
        "https://github.com/octocat/Hello-World.git", owner="octocat", name="Hello-World"
    )
    res = clone(cand, tmp_path / "ws", "arXiv:0000.00000")
    assert len(res.commit_sha) == 40
    assert (res.root / "README").exists()
    assert (res.root / ".git" / "shallow").exists()
