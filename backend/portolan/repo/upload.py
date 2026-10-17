"""Upload your own code: a zip archive, or (local installs only) a folder path (spec §10.3).

Zips are extracted into `~/.portolan/workspace/<paper>/upload-<sha>/` with zip-slip, symlink,
size, count and compression-ratio guards. Local folders are used in place, read-only; nothing is
copied or executed.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from portolan.models.repo import RepoCandidate
from portolan.repo.finder import CODE_EXTENSIONS

MAX_MEMBERS = 20_000
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_RATIO = 200  # uncompressed / compressed, per member (zip-bomb guard)
SKIP_DIRS = {".git", "__MACOSX", "node_modules", ".venv", "venv", "__pycache__"}


class UploadError(Exception):
    """Plain-language problem with uploaded code."""


def safe_dirname(paper_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", paper_id)


def _member_path(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
        return None
    if any(part in SKIP_DIRS for part in p.parts):
        return None
    return p


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _count_code(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() in CODE_EXTENSIONS)


def _strip_single_top_dir(names: list[PurePosixPath]) -> int:
    """GitHub-style zips wrap everything in one folder; drop it."""
    tops = {n.parts[0] for n in names}
    return 1 if len(tops) == 1 and all(len(n.parts) > 1 for n in names) else 0


def extract_zip(
    data: bytes, paper_id: str, workspace: Path, filename: str = "upload.zip"
) -> RepoCandidate:
    digest = hashlib.sha256(data).hexdigest()[:12]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError(f"{filename} isn't a valid zip file.") from None

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise UploadError(f"{filename} has too many files (over {MAX_MEMBERS:,}).")
        members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        total = 0
        for info in infos:
            if info.is_dir() or _is_symlink(info):
                continue
            path = _member_path(info.filename)
            if path is None or info.file_size > MAX_FILE_BYTES:
                continue
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise UploadError(f"{filename} looks like a zip bomb; refusing to extract it.")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise UploadError(f"{filename} is too large once unpacked (over 500 MB).")
            members.append((info, path))
        if not members:
            raise UploadError(f"{filename} has no files Portolan can use.")

        strip = _strip_single_top_dir([p for _, p in members])
        dest = workspace / safe_dirname(paper_id) / f"upload-{digest}"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        root = dest.resolve()
        for info, path in members:
            rel = PurePosixPath(*path.parts[strip:])
            target = (dest / rel).resolve()
            if not target.is_relative_to(root):  # belt and braces against zip-slip
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)

    code = _count_code(dest)
    return RepoCandidate(
        id=f"upload:{digest}",
        url=dest.as_uri(),
        owner="you",
        name=Path(filename).stem,
        source="user",
        official=None,
        official_evidence="uploaded by you",
        status="ok" if code else "placeholder",
        status_reason=None if code else "no source files in the upload",
        host="upload",
        local_path=str(dest),
    )


def use_folder(path: str | Path) -> RepoCandidate:
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise UploadError(f"{folder} isn't a folder Portolan can read.")
    folder = folder.resolve()
    code = _count_code(folder)
    digest = hashlib.sha256(str(folder).encode()).hexdigest()[:12]
    return RepoCandidate(
        id=f"local:{digest}",
        url=folder.as_uri(),
        owner="you",
        name=folder.name,
        source="user",
        official=None,
        official_evidence="a folder on this computer",
        status="ok" if code else "placeholder",
        status_reason=None if code else "no source files in this folder",
        host="local",
        local_path=str(folder),
    )
