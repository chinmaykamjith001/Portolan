"""Repository URL normalization and "is this the paper's own code?" context checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from portolan.models.paper import PaperDoc
from portolan.models.repo import RepoHost

# github.com/<owner> paths that are not users or orgs.
GITHUB_RESERVED = {
    "about", "apps", "blog", "collections", "contact", "customer-stories", "enterprise", "events",
    "explore", "features", "issues", "login", "marketplace", "new", "notifications", "orgs",
    "organizations", "pricing", "pulls", "search", "security", "settings", "site", "sponsors",
    "topics", "trending", "users", "join", "readme", "resources", "solutions", "team",
}  # fmt: skip
NAME = r"[A-Za-z0-9_.\-]+"

# Phrases that mark a link as the paper's own released code (checked near the link).
RELEASE_CONTEXT = re.compile(
    r"\b(?:our|the)\s+(?:code|codes|implementation|source\s+code|models?|package|library|"
    r"framework|toolkit|repository|checkpoints?)\b"
    r"|\bcode\s+(?:is|are|will\s+be|has\s+been)\s+(?:publicly\s+|made\s+)?(?:available|released)"
    r"|\b(?:we|i)\s+(?:release|open[- ]?source|make\s+(?:\w+\s+){0,3}available|provide)\b"
    r"|\b(?:available|released|open[- ]sourced?)\s+(?:at|on|in|via|from)\b"
    r"|\bproject\s+page\b",
    re.IGNORECASE,
)
CONTEXT_CHARS = 220


@dataclass(frozen=True)
class RepoRef:
    host: RepoHost
    owner: str  # GitLab: the full namespace ("group/subgroup")
    name: str

    @property
    def key(self) -> str:
        return f"{self.host}:{self.owner}/{self.name}".lower()

    @property
    def id(self) -> str:
        return f"{self.host}:{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        domain = "github.com" if self.host == "github" else "gitlab.com"
        return f"https://{domain}/{self.owner}/{self.name}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_repo_url(url: str) -> RepoRef | None:
    """https://github.com/Owner/Repo(.git)(/tree/...)? → RepoRef; anything else → None."""
    url = url.strip().rstrip(".,;:)]}>\"'")
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if host == "github.com":
        if len(segments) < 2 or segments[0].lower() in GITHUB_RESERVED:
            return None
        owner, name = segments[0], segments[1].removesuffix(".git")
        if not (re.fullmatch(NAME, owner) and re.fullmatch(NAME, name)):
            return None
        return RepoRef("github", owner, name)
    if host == "gitlab.com":
        # Group paths can nest; "/-/" starts GitLab's own routes (tree, blob, issues...).
        if "-" in segments:
            segments = segments[: segments.index("-")]
        if len(segments) < 2 or segments[0].lower() in {"explore", "users", "help", "dashboard"}:
            return None
        segments[-1] = segments[-1].removesuffix(".git")
        if not all(re.fullmatch(NAME, s) for s in segments):
            return None
        return RepoRef("gitlab", "/".join(segments[:-1]), segments[-1])
    return None


def find_repo_urls(text: str) -> list[str]:
    return re.findall(r"(?:https?://)?(?:www\.)?(?:github|gitlab)\.com/[^\s)\]}>\"'<,;]+", text)


def is_project_page(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    path = urlsplit(url).path.lower()
    if host in {"github.com", "gitlab.com", "arxiv.org", "doi.org", "huggingface.co"}:
        return False
    return (
        host.endswith((".github.io", ".gitlab.io"))
        or "project" in path
        or host.startswith(("sites.google.", "project"))
    )


def link_context(doc: PaperDoc, ref: RepoRef) -> tuple[str, str] | None:
    """Where the paper mentions this repo: (anchor label or "abstract", surrounding text)."""
    needle = ref.full_name.lower()
    sources = [("abstract", doc.abstract)] + [(a.label, a.text) for a in doc.anchors]
    for label, text in sources:
        i = text.lower().find(needle)
        if i >= 0:
            start = max(0, i - CONTEXT_CHARS)
            return label, text[start : i + len(needle) + 60]
    return None


def release_evidence(doc: PaperDoc, ref: RepoRef) -> tuple[bool | None, str]:
    """Is a repo linked in the paper the paper's own code?

    True when the link sits in the abstract or next to release wording ("our code is available
    at ..."); None when it is only mentioned (it may be a baseline or a dataset tool).
    """
    ctx = link_context(doc, ref)
    if ctx is None:
        return None, "linked in the paper"
    label, text = ctx
    if label == "abstract":
        return True, "linked in the paper's abstract"
    if RELEASE_CONTEXT.search(text):
        return True, f"linked in the paper as its code ({label})"
    return None, f"mentioned in the paper ({label}); may be a third-party tool"
