"""Code grounding for a concept (spec §10.7.2).

Looks up the paper's chosen repo and the newest code index stored for it (it never clones; the
index comes from `portolan index` or whatever job indexes the repo), retrieves candidate spans,
asks for line notes in one call, and keeps only what checks out against the stored files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from portolan.code.index_store import IndexStore, split_lines
from portolan.code.retrieval import retrieve
from portolan.detail.explain import DetailError
from portolan.llm.gateway import Gateway
from portolan.llm.prompts import ground_code as ground_prompt
from portolan.llm.prompts.ground_schemas import GroundOut
from portolan.models.code import CodeSpan, LineNote, Symbol
from portolan.models.concept import Concept
from portolan.models.detail import CodeGrounding
from portolan.models.paper import PaperDoc
from portolan.models.repo import RepoCandidate
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.repos import chosen_repo

SYMBOLS_PER_QUERY = 6
CANDIDATE_SYMBOLS = 6
MAX_SPAN_LINES = 80
MAX_NOTES = 8
NOTE_WORDS = 25
TAG_CHARS = 60
MAX_LIBRARY_CALLS = 8
RRF_K = 60
SHA = re.compile(r"[0-9a-f]{40}")
DOTTED = re.compile(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+")

NO_REPO = "No code is linked for this paper."
NOT_INDEXED = "The linked code hasn't been indexed yet."
NOT_IN_CODE = "The code doesn't spell this idea out."
UNVERIFIED = "Nothing in the code could be tied to this concept with confidence."


@dataclass
class Checked:
    spans: list[CodeSpan]
    notes: list[LineNote]
    library_calls: list[str]
    problems: list[str]  # plain-language reasons for dropped items, fed back on the retry


def _concept_only(concept_id: str, summary: str) -> CodeGrounding:
    return CodeGrounding(concept_id=concept_id, grounding="concept_only", summary=summary)


def _latest_commit(db: Database, repo_id: str) -> str | None:
    row = db.fetchone(
        "SELECT commit_sha FROM code_indexes WHERE repo_id = ? ORDER BY created_at DESC LIMIT 1",
        (repo_id,),
    )
    return row["commit_sha"] if row else None


def _passages(doc: PaperDoc, concept: Concept) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for aid in concept.anchors:
        a = doc.anchor(aid)
        if a is not None and a.text:
            out.append({"id": a.id, "label": a.label, "text": a.text[:1200]})
        if len(out) >= 4:
            break
    return out


def _notation(doc: PaperDoc, passages: list[dict[str, str]]) -> dict[str, str]:
    """Symbols that the concept's own passages use, so the notation table stays short."""
    text = " ".join(p["text"] for p in passages)
    used = {k: v for k, v in doc.symbols.items() if k in text}
    return dict(list(used.items())[:20])


def _queries(
    concept: Concept, passages: list[dict[str, str]], symbols: dict[str, str]
) -> list[str]:
    queries = [f"{concept.title} {concept.gist}", concept.title]
    queries += [p["text"][:300] for p in passages[:2]]
    if symbols:
        queries.append(" ".join(f"{k} {v}" for k, v in list(symbols.items())[:6]))
    return queries


def _candidates(store: IndexStore, repo_id: str, sha: str, queries: list[str]) -> list[Symbol]:
    """Symbols ranked across all queries by reciprocal-rank fusion (BM25 only: no model load)."""
    score: dict[str, float] = {}
    by_id: dict[str, Symbol] = {}
    for query in queries:
        hits = retrieve(store, repo_id, sha, query, k=SYMBOLS_PER_QUERY)
        for rank, hit in enumerate(hits, start=1):
            s = hit.symbol
            by_id[s.id] = s
            score[s.id] = score.get(s.id, 0.0) + 1.0 / (RRF_K + rank)
    order = sorted(score, key=lambda sid: (-score[sid], sid))
    return [by_id[sid] for sid in order if by_id[sid].kind != "module"][:CANDIDATE_SYMBOLS]


def _candidate_inputs(
    store: IndexStore, repo_id: str, sha: str, symbols: list[Symbol]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in symbols:
        end = min(s.end_line, s.start_line + ground_prompt.CANDIDATE_LINES - 1)
        lines = store.lines(repo_id, sha, s.path, s.start_line, end)
        if not lines:
            continue
        out.append(
            {
                "path": s.path,
                "start_line": s.start_line,
                "end_line": s.start_line + len(lines) - 1,
                "qualname": s.qualname,
                "lines": [[s.start_line + i, text] for i, text in enumerate(lines)],
            }
        )
    return out


def _trim_words(text: str, limit: int) -> str:
    words = text.split()
    return text.strip() if len(words) <= limit else " ".join(words[:limit]) + "…"


def check(
    out: GroundOut, store: IndexStore, repo_id: str, sha: str, libraries: list[str]
) -> Checked:
    """Drop everything that doesn't match the stored files; remember why."""
    problems: list[str] = []
    n_lines: dict[str, int] = {}
    spans: list[CodeSpan] = []
    seen: set[tuple[str, int, int]] = set()
    for s in out.spans:
        path = s.path.strip().removeprefix("./")
        if path not in n_lines:
            text = store.file_text(repo_id, sha, path)
            n_lines[path] = len(split_lines(text)) if text is not None else 0
        total = n_lines[path]
        if total == 0:
            problems.append(f"The file {s.path!r} is not in the repository.")
            continue
        start, end = max(s.start_line, 1), min(s.end_line, total)
        if start > end:
            problems.append(f"Lines {s.start_line}-{s.end_line} of {path} are outside the file.")
            continue
        end = min(end, start + MAX_SPAN_LINES - 1)
        if (path, start, end) in seen:
            continue
        seen.add((path, start, end))
        spans.append(
            CodeSpan(repo_id=repo_id, commit_sha=sha, path=path, start_line=start, end_line=end)
        )

    notes: list[LineNote] = []
    used: set[tuple[str, int]] = set()
    for n in out.notes:
        # Notes carry no path in the schema: they belong to the span that contains the line. With
        # spans in several files a line number can fit more than one; the first span wins.
        home = next((sp for sp in spans if sp.start_line <= n.line <= sp.end_line), None)
        if home is None:
            problems.append(f"Line {n.line} is not inside any span you chose.")
            continue
        if (home.path, n.line) in used or not n.tag.strip() or not n.note.strip():
            continue
        used.add((home.path, n.line))
        notes.append(
            LineNote(
                line=n.line,
                tag=n.tag.strip()[:TAG_CHARS],
                note=_trim_words(n.note, NOTE_WORDS),
                kind=n.kind,
            )
        )
    notes = sorted(notes, key=lambda n: n.line)[:MAX_NOTES]
    spans = [sp for sp in spans if any(sp.start_line <= n.line <= sp.end_line for n in notes)] or (
        spans if notes else []
    )
    if not notes:
        spans = []

    calls: list[str] = []
    for call in out.library_calls:
        name = call.strip()
        top = name.split(".", 1)[0]
        if not DOTTED.fullmatch(name):
            continue
        if libraries and top not in libraries:
            problems.append(f"{name!r} does not come from a library the repository imports.")
            continue
        if name not in calls:
            calls.append(name)
    return Checked(spans, notes, calls[:MAX_LIBRARY_CALLS], problems)


def blob_links(repo: RepoCandidate, sha: str, paths: list[str]) -> dict[str, str]:
    """File URLs at the commit, for GitHub and GitLab repositories (others get no links)."""
    if repo.host not in ("github", "gitlab") or not SHA.fullmatch(sha):
        return {}
    base = repo.url.rstrip("/").removesuffix(".git")
    if not base.startswith("https://"):
        return {}
    middle = "/blob/" if repo.host == "github" else "/-/blob/"
    return {p: f"{base}{middle}{sha}/{p}" for p in paths}


def _lines_of(store: IndexStore, spans: list[CodeSpan]) -> dict[str, dict[int, str]]:
    code: dict[str, dict[int, str]] = {}
    for sp in spans:
        lines = store.lines(sp.repo_id, sp.commit_sha, sp.path, sp.start_line, sp.end_line)
        target = code.setdefault(sp.path, {})
        for i, text in enumerate(lines):
            target[sp.start_line + i] = text
    return code


def _summary(out: GroundOut, spans: list[CodeSpan]) -> str:
    text = out.summary.strip()
    if text:
        return text
    first = min(spans, key=lambda s: (s.path, s.start_line))
    return f"Lines {first.start_line}-{first.end_line} of {first.path} implement this."


async def ground_code(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, db: Database, concept_id: str
) -> CodeGrounding:
    """Retrieve spans for the concept, ask for line notes, validate them against the index.

    No repo linked or nothing found -> `grounding="concept_only"` (never an error).
    """
    concept = chart.get_concept(doc.paper_id, concept_id)
    if concept is None:
        raise DetailError("That concept isn't on this paper's map.")
    repo = chosen_repo(db, doc.paper_id)
    if repo is None:
        return _concept_only(concept_id, NO_REPO)
    sha = _latest_commit(db, repo.id)
    store = IndexStore(db)
    index = store.load_index(repo.id, sha) if sha else None
    if sha is None or index is None:
        return _concept_only(concept_id, NOT_INDEXED)

    passages = _passages(doc, concept)
    symbols = _notation(doc, passages)
    found = _candidates(store, repo.id, sha, _queries(concept, passages, symbols))
    candidates = _candidate_inputs(store, repo.id, sha, found)
    if not candidates:
        return _concept_only(concept_id, NOT_IN_CODE)  # nothing to show the model: no request

    inputs: dict[str, Any] = {
        "paper_title": doc.title,
        "concept": {"title": concept.title, "gist": concept.gist},
        "passages": passages,
        "symbols": symbols,
        "candidates": candidates,
        "library_modules": index.library_modules,
        "error": None,
    }
    checked = Checked([], [], [], [])
    out = GroundOut(grounding="concept_only", summary="")
    for attempt in (0, 1):
        result = await gateway.call(
            "default", ground_prompt.TEMPLATE, inputs, GroundOut, "P0", doc.paper_id
        )
        out = result.parsed
        checked = check(out, store, repo.id, sha, index.library_modules)
        if out.grounding == "concept_only" and not out.spans:
            return _concept_only(concept_id, out.summary.strip() or NOT_IN_CODE)
        if checked.notes or (out.grounding == "in_library" and checked.library_calls):
            break
        if attempt == 0:  # claimed code but nothing survived: one retry with the reasons
            reasons = " ".join(dict.fromkeys(checked.problems)) or "No note could be verified."
            inputs = {**inputs, "error": reasons}

    if checked.notes:
        return CodeGrounding(
            concept_id=concept_id,
            grounding="in_code",
            spans=checked.spans,
            notes=checked.notes,
            library_calls=checked.library_calls,
            summary=_summary(out, checked.spans),
            code=_lines_of(store, checked.spans),
            links=blob_links(repo, sha, sorted({sp.path for sp in checked.spans})),
        )
    if checked.library_calls:
        return CodeGrounding(
            concept_id=concept_id,
            grounding="in_library",
            library_calls=checked.library_calls,
            summary=out.summary.strip() or f"Done by {checked.library_calls[0]}.",
        )
    return _concept_only(concept_id, UNVERIFIED)
