"""Shallow, hardened git clones for the code index (spec §10.4, §15; docs/notes/m3-externals.md §4).

Hosts ``github`` and ``gitlab`` are cloned with git. Hosts ``upload`` and ``local`` are
already on disk, so no git runs; their "commit" is a stable content hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from portolan.models.repo import RepoCandidate
from portolan.repo.upload import safe_dirname

_SKIPPED_FILE = "portolan-skipped.json"  # kept inside .git so reuse can report skipped_large

# Variables git needs to run and reach the network; everything else (notably GIT_*) is dropped.
_ENV_PASSTHROUGH = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


class CloneError(Exception):
    """A clone failed; the message is plain language for the user."""


@dataclass
class ClonedRepo:
    root: Path
    commit_sha: str
    skipped_large: list[str]


def clone(
    candidate: RepoCandidate,
    workspace: Path,
    paper_id: str,
    *,
    max_file_bytes: int = 1_000_000,
    timeout: float = 300,
    allow_file_protocol: bool = False,
    url_override: str | None = None,
) -> ClonedRepo:
    """Clone ``candidate`` under ``workspace / safe_dirname(paper_id)`` and return where it is.

    ``allow_file_protocol`` and ``url_override`` exist for tests only: together they let a
    ``file://`` URL stand in for the remote.
    """
    if candidate.host in ("upload", "local"):
        return _from_disk(candidate, max_file_bytes)
    label = f"{candidate.owner}/{candidate.name}"
    url = url_override or candidate.url
    scheme = urlparse(url).scheme
    if scheme != "https" and not (allow_file_protocol and scheme == "file"):
        raise CloneError(
            f"Couldn't clone {label}: only https:// repository addresses are supported."
        )
    dest = (
        workspace
        / safe_dirname(paper_id)
        / f"{safe_dirname(candidate.owner)}__{safe_dirname(candidate.name)}"
    )
    with tempfile.TemporaryDirectory(prefix="portolan-nohooks-") as hooks_dir:
        runner = _Git(label, hooks_dir, timeout, allow_file_protocol)
        existing = _reuse(runner, dest)
        if existing is not None:
            return existing
        if dest.exists():
            _rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            return _fresh_clone(runner, url, dest, candidate.default_branch, max_file_bytes)
        except BaseException:
            _rmtree(dest)
            raise


class _Git:
    def __init__(self, label: str, hooks_dir: str, timeout: float, allow_file: bool) -> None:
        self.label = label
        self.timeout = timeout
        self.config = [
            "-c",
            f"core.hooksPath={Path(hooks_dir).as_posix()}",
            "-c",
            "core.longpaths=true",
            "-c",
            "core.symlinks=false",
            "-c",
            f"protocol.file.allow={'always' if allow_file else 'never'}",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "credential.helper=",
            "-c",
            "http.lowSpeedLimit=1000",
            "-c",
            "http.lowSpeedTime=30",
        ]

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
        if extra:
            env.update(extra)
        return env

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        stdin: str | None = None,
        no_lazy_fetch: bool = True,
    ) -> str:
        env = self.env({"GIT_NO_LAZY_FETCH": "1"} if no_lazy_fetch else None)
        try:
            proc = subprocess.run(
                ["git", *self.config, *args],
                cwd=cwd,
                env=env,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise CloneError(
                f"Couldn't clone {self.label}: git isn't installed or isn't on PATH."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise CloneError(
                f"Couldn't clone {self.label}: it took longer than {self.timeout:.0f} seconds."
            ) from e
        if proc.returncode != 0:
            raise CloneError(_explain(self.label, proc.stderr))
        return proc.stdout


def _explain(label: str, stderr: str) -> str:
    low = stderr.lower()
    if any(
        s in low
        for s in (
            "not found",
            "could not read username",
            "terminal prompts disabled",
            "authentication failed",
            "does not appear to be a git repository",
            "does not exist",
        )
    ):
        return f"Couldn't clone {label}: repository not found or private."
    if any(
        s in low for s in ("could not resolve host", "unable to access", "timed out", "connection")
    ):
        return f"Couldn't clone {label}: network problem reaching the host."
    if "remote branch" in low:
        return f"Couldn't clone {label}: the default branch wasn't found."
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    detail = lines[-1] if lines else "git failed without a message"
    return f"Couldn't clone {label}: {detail}"


def _fresh_clone(
    git: _Git, url: str, dest: Path, branch: str | None, max_file_bytes: int
) -> ClonedRepo:
    args = [
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--no-recurse-submodules",
        f"--filter=blob:limit={max_file_bytes}",
        "--template=",
        "--no-checkout",
    ]
    if branch:
        args += ["--branch", branch]
    # No-lazy-fetch must stay off here: the clone itself is the fetch.
    git.run([*args, "--", url, str(dest)], no_lazy_fetch=False)
    skipped = _omitted_paths(git, dest)
    if skipped:
        patterns = ["/*", *(f"!/{_escape_pattern(p)}" for p in skipped)]
        git.run(
            ["sparse-checkout", "set", "--no-cone", "--stdin"],
            cwd=dest,
            stdin="\n".join(patterns) + "\n",
        )
    git.run(["checkout", "-q", "HEAD"], cwd=dest)
    # Servers that ignore --filter send every blob; drop any oversize file that got through.
    for path in _oversize_files(dest, max_file_bytes):
        rel = path.relative_to(dest).as_posix()
        path.unlink()
        if rel not in skipped:
            skipped.append(rel)
    skipped.sort()
    sha = git.run(["rev-parse", "HEAD"], cwd=dest).strip()
    (dest / ".git" / _SKIPPED_FILE).write_text(json.dumps(skipped), encoding="utf-8")
    return ClonedRepo(root=dest, commit_sha=sha, skipped_large=skipped)


def _reuse(git: _Git, dest: Path) -> ClonedRepo | None:
    if not (dest / ".git").is_dir():
        return None
    try:
        sha = git.run(["rev-parse", "HEAD"], cwd=dest).strip()
        skipped = json.loads((dest / ".git" / _SKIPPED_FILE).read_text(encoding="utf-8"))
    except (CloneError, OSError, ValueError):
        return None  # half-finished or damaged: reclone
    if not isinstance(skipped, list) or not all(isinstance(s, str) for s in skipped):  # pyright: ignore[reportUnknownVariableType]
        return None
    return ClonedRepo(root=dest, commit_sha=sha, skipped_large=list(skipped))  # pyright: ignore[reportUnknownArgumentType]


def _omitted_paths(git: _Git, dest: Path) -> list[str]:
    """Paths whose blobs the server left out because they exceed the size limit."""
    out = git.run(["rev-list", "--objects", "--missing=print", "HEAD"], cwd=dest)
    omitted = {line[1:].strip() for line in out.splitlines() if line.startswith("?")}
    if not omitted:
        return []
    tree = git.run(["ls-tree", "-r", "-z", "HEAD"], cwd=dest)
    paths: list[str] = []
    for entry in tree.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and parts[2] in omitted:
            paths.append(path)
    return sorted(paths)


def _escape_pattern(path: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(path):
        if ch in "*?[]\\" or (i == 0 and ch in "!# "):
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _oversize_files(root: Path, limit: int) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root and ".git" in dirnames:
            dirnames.remove(".git")
        for f in filenames:
            p = Path(dirpath) / f
            try:
                if p.stat().st_size > limit:
                    found.append(p)
            except OSError:
                continue
    return found


def _from_disk(candidate: RepoCandidate, max_file_bytes: int) -> ClonedRepo:
    label = f"{candidate.owner}/{candidate.name}"
    if not candidate.local_path:
        raise CloneError(f"Couldn't open {label}: no folder is recorded for it.")
    root = Path(candidate.local_path)
    if not root.is_dir():
        raise CloneError(f"Couldn't open {label}: the folder {root} no longer exists.")
    entries: list[tuple[str, int, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((p.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    entries.sort()
    h = hashlib.sha256()
    for rel, size, mtime in entries:
        h.update(f"{rel}\0{size}\0{mtime}\n".encode())
    skipped = [rel for rel, size, _ in entries if size > max_file_bytes]
    return ClonedRepo(root=root, commit_sha=f"content:{h.hexdigest()}", skipped_large=skipped)


def _rmtree(path: Path) -> None:
    def onexc(func: Callable[[str], object], p: str, _exc: BaseException) -> None:
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)
