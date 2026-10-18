"""Where FAQ questions come from (spec §10.10). Every source is optional: each function returns
[] on any failure (no network, not found, rate limited, unexpected shape) and never raises.

Verification status of the external shapes (see docs/DECISIONS.md): the OpenReview v2 and GitHub
REST parsing follows their documentation and is covered by mocked responses only. The owner
should run `pytest -m network tests/test_network_faq.py` once to confirm the live shapes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import quote

from portolan.faq.text import clip, jaccard, tokens
from portolan.ingest.http import PoliteClient
from portolan.models.finding import Finding, SourceKind
from portolan.models.repo import RepoCandidate

OPENREVIEW_API = "https://api2.openreview.net"
OPENREVIEW_SITE = "https://openreview.net"
GITHUB_API = "https://api.github.com"
TTL = 24 * 3600.0

MAX_REVIEW_ITEMS = 8
MAX_ISSUE_ITEMS = 6
ISSUE_PAGE = 30
TITLE_MATCH = 0.85
MIN_QUESTION_WORDS = 5
MAX_QUESTION_WORDS = 70


@dataclass
class RawItem:
    """A question collected from a source, before clustering and answering."""

    question: str
    source_kind: SourceKind
    source_label: str
    source_url: str | None = None
    source_text: str = ""  # the source's own reply (untrusted data)
    weight: float = 0.0  # popularity: more discussion first
    answer: str | None = None  # a ready-made answer (findings); no LLM needed
    anchors: list[str] = field(default_factory=list[str])
    concept_ids: list[str] = field(default_factory=list[str])


def _json(content: bytes) -> Any:
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return None


def as_dict(x: object) -> dict[str, Any]:
    """`x` if it is a JSON object, else {} (untrusted JSON, so shapes are never assumed)."""
    return cast("dict[str, Any]", x) if isinstance(x, dict) else {}


def as_list(x: object) -> list[Any]:
    return cast("list[Any]", x) if isinstance(x, list) else []


# -- OpenReview ------------------------------------------------------------------------------


def _val(content: object, key: str) -> str:
    """Field of an OpenReview note's `content`: v2 wraps values as {"value": ...}; v1 doesn't."""
    raw: Any = as_dict(content).get(key)
    wrapped = as_dict(raw)
    if wrapped:
        raw = wrapped.get("value")
    listed = as_list(raw)
    if listed:
        raw = "\n".join(str(x) for x in listed)
    return str(raw).strip() if isinstance(raw, str | int | float) else ""


def _invitations(note: dict[str, Any]) -> list[str]:
    many = [str(i) for i in as_list(note.get("invitations"))]
    if many:
        return many
    one = note.get("invitation")
    return [str(one)] if isinstance(one, str) else []


def _is_review(note: dict[str, Any]) -> bool:
    return any(i.endswith(("Official_Review", "Review")) for i in _invitations(note))


def _is_author(note: dict[str, Any]) -> bool:
    return any("Authors" in str(sig) for sig in as_list(note.get("signatures")))


REPLY_FIELDS = ("comment", "rebuttal", "reply", "response", "text")
QUESTION_FIELDS = ("questions", "question", "questions_to_authors")
_MARKER = re.compile(r"^\s*(?:[-*•]|\(?\d+[.)]|\(?[a-z][.)])\s+")


def split_questions(text: str) -> list[str]:
    """Individual questions from a review's free-text "questions" field."""
    out: list[str] = []
    for line in re.split(r"\n+", text):
        line = _MARKER.sub("", line).strip()
        if "?" not in line:
            continue
        for part in re.findall(r"[^?]*\?", line):
            part = part.strip()
            if MIN_QUESTION_WORDS <= len(part.split()) <= MAX_QUESTION_WORDS:
                out.append(part)
    return out


def _author_replies(notes: list[dict[str, Any]], review_id: str) -> str:
    """Text of every author note somewhere under `review_id` in the reply tree."""
    parent: dict[str, str] = {
        str(n["id"]): str(n["replyto"]) for n in notes if n.get("id") and n.get("replyto")
    }

    def under(note_id: str) -> bool:
        seen: set[str] = set()
        while note_id in parent and note_id not in seen:
            seen.add(note_id)
            note_id = parent[note_id]
            if note_id == review_id:
                return True
        return False

    texts: list[str] = []
    for n in notes:
        if not _is_author(n) or not under(str(n.get("id", ""))):
            continue
        body = "\n".join(t for f in REPLY_FIELDS if (t := _val(n.get("content"), f)))
        if body:
            texts.append(body)
    return "\n\n".join(texts)


async def reviewer_items(http: PoliteClient, title: str) -> list[RawItem]:
    """Reviewer questions that the authors answered, from the paper's OpenReview forum."""
    try:
        found = await http.get(
            f"{OPENREVIEW_API}/notes/search",
            params={"term": title, "content": "title", "source": "forum", "limit": "10"},
            ttl=TTL,
        )
        candidates = as_list(as_dict(_json(found.content)).get("notes")) if found.ok else []
        want = set(tokens(title))
        forum_id = ""
        for n in candidates:
            note = as_dict(n)
            if jaccard(want, set(tokens(_val(note.get("content"), "title")))) >= TITLE_MATCH:
                forum_id = str(note.get("forum") or note.get("id") or "")
                break
        if not forum_id:
            return []
        forum = await http.get(
            f"{OPENREVIEW_API}/notes", params={"forum": forum_id, "limit": "1000"}, ttl=TTL
        )
        thread = as_list(as_dict(_json(forum.content)).get("notes")) if forum.ok else []
        all_notes = [d for d in (as_dict(n) for n in thread) if d]
    except Exception:
        return []

    url = f"{OPENREVIEW_SITE}/forum?id={quote(forum_id)}"
    items: list[RawItem] = []
    for review in all_notes:
        if not _is_review(review):
            continue
        reply = _author_replies(all_notes, str(review.get("id", "")))
        if not reply:
            continue  # unanswered questions say nothing yet
        text = "\n".join(t for f in QUESTION_FIELDS if (t := _val(review.get("content"), f)))
        for q in split_questions(text):
            items.append(
                RawItem(
                    question=q,
                    source_kind="reviewer_discussion",
                    source_label="From reviewer discussion",
                    source_url=url,
                    source_text=clip(reply, 2500),
                    weight=float(len(reply)),
                )
            )
    items.sort(key=lambda i: -i.weight)
    return items[:MAX_REVIEW_ITEMS]


# -- GitHub issues ---------------------------------------------------------------------------

_QUESTION_START = re.compile(
    r"^(how|why|what|which|where|when|who|does|do|did|is|are|can|could|should|would|will)\b", re.I
)
MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}


def looks_like_question(title: str, body: str, labels: list[str]) -> bool:
    if any("question" in label.lower() for label in labels):
        return True
    t = title.strip()
    return t.endswith("?") or bool(_QUESTION_START.match(t)) or ("?" in t and "?" in body[:400])


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"  # sent to api.github.com only
    return h


async def github_issue_items(http: PoliteClient, repo: RepoCandidate | None) -> list[RawItem]:
    """Popular question-like issues of the paper's repo that got a reply."""
    if repo is None or repo.host != "github" or not repo.owner or not repo.name:
        return []
    base = f"{GITHUB_API}/repos/{quote(repo.owner)}/{quote(repo.name)}"
    try:
        listing = await http.get(
            f"{base}/issues",
            params={
                "state": "all",
                "sort": "comments",
                "direction": "desc",
                "per_page": str(ISSUE_PAGE),
            },
            headers=_headers(),
            ttl=TTL,
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for raw in as_list(_json(listing.content)) if listing.ok else []:
            issue = as_dict(raw)
            if not issue or "pull_request" in issue or not issue.get("comments"):
                continue
            labels = [str(as_dict(lbl).get("name", "")) for lbl in as_list(issue.get("labels"))]
            title, body = str(issue.get("title") or ""), str(issue.get("body") or "")
            if not looks_like_question(title, body, labels):
                continue
            thumbs = int(as_dict(issue.get("reactions")).get("total_count") or 0)
            scored.append((float(issue["comments"]) + thumbs, issue))
        scored.sort(key=lambda s: (-s[0], int(s[1].get("number", 0))))
        items: list[RawItem] = []
        for weight, issue in scored[:MAX_ISSUE_ITEMS]:
            number = int(issue["number"])
            comments = await http.get(
                f"{base}/issues/{number}/comments",
                params={"per_page": "10"},
                headers=_headers(),
                ttl=TTL,
            )
            thread = [as_dict(c) for c in as_list(_json(comments.content))] if comments.ok else []
            replies = [c for c in thread if c.get("body")]
            best = next((c for c in replies if c.get("author_association") in MAINTAINER), None)
            if best is None and replies:
                best = replies[0]
            if best is None:
                continue
            url = str(
                issue.get("html_url")
                or f"https://github.com/{repo.owner}/{repo.name}/issues/{number}"
            )
            items.append(
                RawItem(
                    question=str(issue["title"]).strip(),
                    source_kind="github_issue",
                    source_label=f"From GitHub issue #{number}",
                    source_url=url,
                    source_text=clip(f"{issue.get('body') or ''}\n\nReply: {best['body']}", 2500),
                    weight=weight,
                )
            )
        return items
    except Exception:
        return []


# -- Findings --------------------------------------------------------------------------------


def finding_items(findings: list[Finding]) -> list[RawItem]:
    """Each mismatch (or code behavior the paper never mentions) becomes a question; the answer
    is the finding's own evidence, so no LLM call is needed."""
    out: list[RawItem] = []
    for f in findings:
        claim = f.claim_text.strip().rstrip(".")
        if f.verdict == "mismatch":
            question = f"Does the code use {claim} as in the paper?"
            lead = "Not exactly."
        elif f.verdict == "undocumented_in_paper":
            question = f"Does the code do something the paper doesn't describe: {claim}?"
            lead = "Yes."
        else:
            continue
        out.append(
            RawItem(
                question=question,
                source_kind="finding",
                source_label="Finding",
                answer=f"{lead} {f.evidence.strip()}",
                anchors=[f.paper_anchor] if f.paper_anchor else [],
                concept_ids=list(f.concept_ids),
                weight=1000.0 + f.confidence,
            )
        )
    return out
