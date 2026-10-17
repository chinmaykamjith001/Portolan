"""Synthetic ingestion fixtures, generated at test time (no third-party files committed)."""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pymupdf

FIXTURES = Path(__file__).parent / "fixtures" / "papers"


def latex_files() -> dict[str, bytes]:
    root = FIXTURES / "tiny_latex"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.tex")}


def latex_tarball(extra: dict[str, bytes] | None = None, *, evil: bool = False) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in {**latex_files(), **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if evil:
            bad = b"\\section{Evil}"
            info = tarfile.TarInfo("../evil.tex")
            info.size = len(bad)
            tar.addfile(info, io.BytesIO(bad))
            link = tarfile.TarInfo("link.tex")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
    return buf.getvalue()


def gz_single_tex() -> bytes:
    return gzip.compress(latex_files()["main.tex"].replace(b"\\input{sections/method}", b""))


LINES: list[tuple[str, float, bool]] = [
    ("A Synthetic PDF Paper", 18, True),
    ("Abstract", 12, True),
    ("We study a toy model and release code at https://github.com/example/pdfpaper.", 10, False),
    ("1 Introduction", 12, True),
    ("Attention scores compare queries with keys. This paragraph is body text.", 10, False),
    ("2 Method", 12, True),
    ("2.1 Scores", 11, True),
    ("The score is computed as follows for every pair of positions.", 10, False),
    ("S = Q K^T / sqrt(d) (1)", 10, False),
    ("Table 1: Settings used in training.", 10, False),
    ("References", 12, True),
    ("[1] A. Person. A cited work. 2020.", 10, False),
    ("A Proofs", 12, True),
    ("The proof is short.", 10, False),
]


def synthetic_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 60.0
    for text, size, bold in LINES:
        if y > 760:
            page = doc.new_page()
            y = 60.0
        page.insert_text((60, y), text, fontsize=size, fontname="hebo" if bold else "helv")
        y += 48
    data = doc.tobytes()
    doc.close()
    return data
