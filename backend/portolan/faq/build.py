"""FAQ (spec §10.10): collect → cluster → answer → link, plus the Ask box.

Request budget (free tier, §5): at most ceil(18 / 6) = 3 answer calls plus 1 anticipated call per
build, all P2; the Ask box is one P0 call. Findings need no LLM at all.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from portolan.code.index_store import IndexStore
from portolan.code.retrieval import retrieve
from portolan.detail.explain import DetailError
from portolan.faq.cluster import Cluster, cluster_items
from portolan.faq.sources import finding_items, github_issue_items, reviewer_items
from portolan.faq.text import anchor_passages, clip, passage, rank_texts, stable_id, tokens
from portolan.ingest.http import PoliteClient
from portolan.llm.gateway import Gateway
from portolan.llm.prompts import ask_answer, faq_answer, faq_anticipated
from portolan.llm.prompts.faq_schemas import AnswerBatchOut, AnticipatedBatchOut, AskOut
from portolan.llm.types import LLMError
from portolan.models.concept import Concept
from portolan.models.finding import FAQItem
from portolan.models.paper import PaperDoc
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.faq import FaqStore
from portolan.store.findings import FindingStore
from portolan.store.repos import chosen_repo

MAX_ITEMS = 30
MAX_CLUSTERS = 18
BATCH = faq_answer.CLUSTERS
ANTICIPATED = faq_anticipated.CONCEPTS
BUDGET_FRACTION = 0.8  # skip the optional anticipated call once this much of the day is used
PASSAGES_PER_CLUSTER = 3

UNSUPPORTED = "The paper and code don't answer this."
ASK_LABEL = "Your question"


def ancestors(doc: PaperDoc, anchors: list[str]) -> list[str]:
    """The enclosing sections of the given anchors (a paragraph under §3.2 counts for §3.2)."""
    out: list[str] = []
    for aid in anchors:
        a = doc.anchor(aid)
        while a is not None and a.parent_id is not None:
            out.append(a.parent_id)
            a = doc.anchor(a.parent_id)
    return list(dict.fromkeys(out))


def link_concepts(
    concepts: dict[str, Concept], text: str, anchors: list[str], enclosing: list[str] | None = None
) -> list[str]:
    """Concepts that share a cited anchor (or its section) with the item, or are named in its
    text, best first. A shared anchor outweighs a shared section."""
    cited, around, lowered = set(anchors), set(enclosing or []), text.lower()
    scored: list[tuple[int, int, str]] = []
    for c in concepts.values():
        own = set(c.anchors)
        score = 3 * len(cited & own) + len(around & own) + (1 if c.title.lower() in lowered else 0)
        if score:
            scored.append((-score, c.depth, c.id))
    return [cid for _, _, cid in sorted(scored)[:3]]


def _budget_allows(gateway: Gateway) -> bool:
    try:
        used = sum(int(r["requests"]) for r in gateway.limiter.budget.today())
        return used < gateway.config.llm.limits.rpd * BUDGET_FRACTION
    except Exception:
        return True


def _item_id(kind: str, question: str, url: str | None) -> str:
    return stable_id("faq", kind, " ".join(question.lower().split()), url or "")


async def build_faq(
    doc: PaperDoc,
    gateway: Gateway,
    chart: ChartStore,
    db: Database,
    http: PoliteClient,
    on_item: Callable[[FAQItem], None],
) -> None:
    store = FaqStore(db)
    paper_id = doc.paper_id
    concepts = chart.concepts(paper_id)
    saved: dict[str, FAQItem] = {}
    valid_anchors = {a.id for a in doc.anchors}

    def emit(item: FAQItem) -> None:
        if item.id in saved or len(saved) >= MAX_ITEMS:
            return
        saved[item.id] = item
        store.save(paper_id, item)
        on_item(item)

    raw = finding_items(FindingStore(db).all(paper_id))
    raw += await reviewer_items(http, doc.title)
    raw += await github_issue_items(http, chosen_repo(db, paper_id))
    clusters = cluster_items(raw)[:MAX_CLUSTERS]

    # 1. Findings answer themselves (no request), so they appear first.
    pending: list[Cluster] = []
    for cluster in clusters:
        rep = cluster.rep
        if rep.answer is None:
            pending.append(cluster)
            continue
        anchors = [a for a in rep.anchors if a in valid_anchors]
        emit(
            FAQItem(
                id=_item_id(rep.source_kind, rep.question, rep.source_url),
                question=rep.question,
                answer_markdown=rep.answer,
                source_kind=rep.source_kind,
                source_label=rep.source_label,
                source_url=rep.source_url,
                concept_ids=rep.concept_ids
                or link_concepts(concepts, rep.question, anchors, ancestors(doc, anchors)),
                anchors_cited=anchors,
            )
        )

    # 2. Reviewer and issue questions: answers written in batches of at most six.
    llm_ok = True
    for start in range(0, len(pending), BATCH):
        if not llm_ok:
            break
        batch = pending[start : start + BATCH]
        passages = [
            [
                passage(a, faq_answer.PASSAGE_CHARS)
                for a in anchor_passages(
                    doc,
                    f"{c.rep.question} {clip(c.source_text, 300)}",
                    PASSAGES_PER_CLUSTER,
                )
            ]
            for c in batch
        ]
        inputs: dict[str, Any] = {
            "paper_title": doc.title,
            "clusters": [
                {
                    "index": i,
                    "kind": c.rep.source_kind,
                    "question": c.rep.question,
                    "source_text": c.source_text,
                    "passages": passages[i],
                }
                for i, c in enumerate(batch)
            ],
        }
        try:
            result = await gateway.call(
                "default", faq_answer.TEMPLATE, inputs, AnswerBatchOut, "P2", paper_id
            )
        except LLMError:
            llm_ok = False  # keep what we have; the FAQ can be rebuilt later from the cache
            continue
        out: AnswerBatchOut = result.parsed
        for a in out.items:
            if not 0 <= a.cluster < len(batch) or not a.keep or not a.answer.strip():
                continue
            rep = batch[a.cluster].rep
            given = {p["id"] for p in passages[a.cluster]}
            anchors = [x for x in dict.fromkeys(a.anchors) if x in given]
            if not anchors and not rep.source_url:
                continue  # an answer must cite an anchor or a URL (§10.10)
            question = a.question.strip() or rep.question
            emit(
                FAQItem(
                    id=_item_id(rep.source_kind, rep.question, rep.source_url),
                    question=question,
                    answer_markdown=a.answer.strip(),
                    source_kind=rep.source_kind,
                    source_label=rep.source_label,
                    source_url=rep.source_url,
                    concept_ids=link_concepts(
                        concepts, f"{question} {a.answer}", anchors, ancestors(doc, anchors)
                    ),
                    anchors_cited=anchors,
                )
            )

    # 3. Anticipated questions: one optional call for concepts nothing else covered.
    room = min(ANTICIPATED, MAX_ITEMS - len(saved))
    if llm_ok and room > 0 and _budget_allows(gateway):
        await _anticipated(doc, gateway, concepts, saved, room, emit)


def _confusing_concepts(
    concepts: dict[str, Concept], covered: set[str], limit: int
) -> list[Concept]:
    """Concepts a newcomer is likely to trip on: ones with layers beneath them, or that the code
    doesn't spell out. Shallow ones first; anything already in the FAQ is skipped."""
    ranked = sorted(
        (c for c in concepts.values() if c.id not in covered and c.depth <= 1),
        key=lambda c: (
            -(2 if c.has_layer else 0) - (1 if c.grounding in ("concept_only", "unknown") else 0),
            c.depth,
            c.id,
        ),
    )
    return ranked[:limit]


async def _anticipated(
    doc: PaperDoc,
    gateway: Gateway,
    concepts: dict[str, Concept],
    saved: dict[str, FAQItem],
    room: int,
    emit: Callable[[FAQItem], None],
) -> None:
    covered = {cid for item in saved.values() for cid in item.concept_ids}
    chosen: list[tuple[Concept, list[dict[str, str]]]] = []
    for c in _confusing_concepts(concepts, covered, room * 2):
        found = [doc.anchor(a) for a in c.anchors]
        anchors = [a for a in found if a is not None and a.text]
        if not anchors:
            anchors = anchor_passages(doc, f"{c.title} {c.gist}", PASSAGES_PER_CLUSTER)
        if anchors:
            chosen.append((c, [passage(a, faq_anticipated.PASSAGE_CHARS) for a in anchors[:3]]))
        if len(chosen) >= room:
            break
    if not chosen:
        return
    inputs: dict[str, Any] = {
        "paper_title": doc.title,
        "concepts": [
            {"index": i, "title": c.title, "gist": c.gist, "passages": ps}
            for i, (c, ps) in enumerate(chosen)
        ],
    }
    try:
        result = await gateway.call(
            "default", faq_anticipated.TEMPLATE, inputs, AnticipatedBatchOut, "P2", doc.paper_id
        )
    except LLMError:
        return
    out: AnticipatedBatchOut = result.parsed
    for a in out.items:
        if not 0 <= a.concept < len(chosen) or not a.answer.strip() or not a.question.strip():
            continue
        concept, ps = chosen[a.concept]
        given = {p["id"] for p in ps}
        anchors = [x for x in dict.fromkeys(a.anchors) if x in given]
        if not anchors:
            continue  # anticipated answers have no URL to fall back on
        emit(
            FAQItem(
                id=_item_id("anticipated", a.question, None),
                question=a.question.strip(),
                answer_markdown=a.answer.strip(),
                source_kind="anticipated",
                source_label="Anticipated",
                concept_ids=[
                    concept.id,
                    *[
                        c
                        for c in link_concepts(
                            concepts, a.question, anchors, ancestors(doc, anchors)
                        )
                        if c != concept.id
                    ],
                ][:3],
                anchors_cited=anchors,
            )
        )


# -- the Ask box -----------------------------------------------------------------------------


def _latest_commit(db: Database, repo_id: str) -> str | None:
    row = db.fetchone(
        "SELECT commit_sha FROM code_indexes WHERE repo_id = ? ORDER BY created_at DESC LIMIT 1",
        (repo_id,),
    )
    return row["commit_sha"] if row else None


def _code_candidates(db: Database, paper_id: str, question: str) -> list[dict[str, Any]]:
    repo = chosen_repo(db, paper_id)
    sha = _latest_commit(db, repo.id) if repo else None
    if repo is None or sha is None:
        return []
    store = IndexStore(db)
    out: list[dict[str, Any]] = []
    try:
        hits = retrieve(store, repo.id, sha, question, k=ask_answer.CODE + 2)
    except Exception:
        return []
    for hit in hits:
        s = hit.symbol
        if s.kind == "module":
            continue
        end = min(s.end_line, s.start_line + ask_answer.CODE_LINES - 1)
        lines = store.lines(repo.id, sha, s.path, s.start_line, end)
        if lines:
            out.append(
                {
                    "path": s.path,
                    "start_line": s.start_line,
                    "end_line": s.start_line + len(lines) - 1,
                    "qualname": s.qualname,
                    "lines": [[s.start_line + i, t] for i, t in enumerate(lines)],
                }
            )
        if len(out) >= ask_answer.CODE:
            break
    return out


def _unsupported(question: str) -> FAQItem:
    return FAQItem(
        id=stable_id("ask", " ".join(question.lower().split())),
        question=question,
        answer_markdown=UNSUPPORTED,
        source_kind="user_asked",
        source_label=ASK_LABEL,
        supported=False,
    )


async def answer_question(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, db: Database, question: str
) -> FAQItem:
    """Answer from retrieved sources only; say so plainly when they don't answer it."""
    question = " ".join(question.split())
    min_hits = 1 if len(set(tokens(question))) <= 2 else 2
    anchors = anchor_passages(doc, question, ask_answer.PASSAGES, min_hits=min_hits)
    stored = [i for i in FaqStore(db).all(doc.paper_id) if i.supported]
    faq_hits = [
        stored[i]
        for i in rank_texts(
            question, [f"{i.question} {i.answer_markdown}" for i in stored], ask_answer.FAQ,
            min_hits=min_hits,
        )
    ]  # fmt: skip
    code = _code_candidates(db, doc.paper_id, question)
    if not anchors and not faq_hits and not code:
        return _unsupported(question)

    passages = [passage(a, ask_answer.PASSAGE_CHARS) for a in anchors]
    inputs: dict[str, Any] = {
        "paper_title": doc.title,
        "question": question,
        "passages": passages,
        "code": code,
        "faq": [
            {"id": i.id, "question": i.question, "answer": clip(i.answer_markdown, 700)}
            for i in faq_hits
        ],
    }
    try:
        result = await gateway.call(
            "default", ask_answer.TEMPLATE, inputs, AskOut, "P0", doc.paper_id
        )
    except LLMError as exc:
        raise DetailError("Couldn't answer that right now. Try again in a moment.") from exc
    out: AskOut = result.parsed

    given_anchors = {p["id"] for p in passages}
    cited = [a for a in dict.fromkeys(out.anchors) if a in given_anchors]
    by_id = {i.id: i for i in faq_hits}
    cited_faq = [by_id[f] for f in dict.fromkeys(out.faq) if f in by_id]
    spans = {f"{c['path']}:{c['start_line']}-{c['end_line']}": c for c in code}
    cited_code = [spans[c] for c in dict.fromkeys(out.code) if c in spans]
    answer = out.answer.strip()
    if not out.supported or not answer or not (cited or cited_faq or cited_code):
        return _unsupported(question)

    if cited_code:
        refs = ", ".join(
            f"`{c['path']}` lines {c['start_line']}-{c['end_line']}" for c in cited_code
        )
        answer = f"{answer}\n\nCode: {refs}"
    url = next((f.source_url for f in cited_faq if f.source_url), None)
    return FAQItem(
        id=stable_id("ask", " ".join(question.lower().split())),
        question=question,
        answer_markdown=answer,
        source_kind="user_asked",
        source_url=url,
        source_label=ASK_LABEL,
        concept_ids=link_concepts(
            chart.concepts(doc.paper_id), f"{question} {answer}", cited, ancestors(doc, cited)
        ),
        anchors_cited=cited,
    )
