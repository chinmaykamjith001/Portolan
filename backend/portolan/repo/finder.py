"""Find, validate and rank a paper's code (spec §8 `repo.finder`, §10.3).

Sources, most confident first:
  1. repo links in the paper (and on its project page);
  2. the Hugging Face Papers API (`githubRepo`);
  3. GitHub repository search for the arXiv id (then the exact title) in READMEs.
The Papers with Code archive (MAY) is not used yet — see docs/DECISIONS.md.

Each candidate is validated (exists / renamed / dead, placeholder or real code), classified
(official, unknown, unofficial) and ranked; a plain-language `RepoStatus` sums it up.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from portolan.ingest.http import FetchError, PoliteClient
from portolan.models.paper import PaperDoc
from portolan.models.repo import RepoCandidate, RepoHealth, RepoSource, RepoStatus
from portolan.repo.github import GitHubClient, GitHubRateLimited, RepoInfo, Tree
from portolan.repo.gitlab import GitLabClient
from portolan.repo.links import (
    RepoRef,
    find_repo_urls,
    is_project_page,
    parse_repo_url,
    release_evidence,
)

HF_PAPERS = "https://huggingface.co/api/papers/{arxiv_id}"
MAX_PROJECT_PAGES = 2
MAX_SEARCH_RESULTS = 8
MAX_VALIDATE = 6
MAX_OWNER_LOOKUPS_FROM_SEARCH = 2  # /users/{login} calls are the costliest part on 60 req/h

CODE_EXTENSIONS = (
    ".py", ".ipynb", ".pyx", ".c", ".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".java", ".jl",
    ".js", ".ts", ".rs", ".go", ".m", ".r", ".lua", ".scala", ".swift", ".kt", ".sh",
)  # fmt: skip
PLACEHOLDER_PHRASES = re.compile(
    r"coming\s+soon|to\s+be\s+released|will\s+be\s+(?:released|available|made\s+(?:public|available))"
    r"|stay\s+tuned|under\s+construction|code\s+release\s+(?:is\s+)?(?:pending|planned)|\bTBA\b|\bTODO\b",
    re.IGNORECASE,
)

OFFICIAL_RANK = {True: 0, None: 1, False: 2}
SOURCE_RANK: dict[RepoSource, int] = {
    "user": 0, "paper_link": 1, "hf_papers": 2, "pwc_archive": 3, "github_search": 4,
}  # fmt: skip


@dataclass
class _Lead:
    """A candidate before validation."""

    ref: RepoRef
    source: RepoSource
    official: bool | None
    evidence: str
    search_info: RepoInfo | None = None  # search results already carry repo metadata
    notes: list[str] = field(default_factory=list[str])


@dataclass
class FinderResult:
    candidates: list[RepoCandidate]
    status: RepoStatus
    warnings: list[str] = field(default_factory=list[str])

    @property
    def best(self) -> RepoCandidate | None:
        return next((c for c in self.candidates if c.status == "ok"), None)


# -- helpers ------------------------------------------------------------------------------


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def owner_matches_author(login: str, display_name: str | None, authors: list[str]) -> str | None:
    """Return the matching author's name when the repo owner looks like one of the authors."""
    login_n = re.sub(r"[^a-z]", "", _ascii(login))
    name_tokens = re.findall(r"[a-z]+", _ascii(display_name or ""))
    for author in authors:
        tokens = re.findall(r"[a-z]+", _ascii(author))
        if len(tokens) < 2:
            continue
        first, last = tokens[0], tokens[-1]
        # Display name: same family name and same first initial ("Edward J. Hu" ~ "Edward Hu").
        if name_tokens and last in name_tokens and name_tokens[0][0] == first[0]:
            return author
        # Login: "edwardjhu", "edward-hu", "hu-edward", "ehu2021" (long family names only).
        if len(first) >= 3 and login_n.startswith(first) and login_n.endswith(last):
            return author
        if len(first) >= 3 and len(last) >= 4 and first in login_n and last in login_n:
            return author
        if len(last) >= 4 and login_n.startswith(first[0] + last):
            return author
    return None


def health(tree: Tree | None, readme: str | None) -> tuple[RepoHealth, str | None]:
    if tree is None:
        return "unknown", "couldn't list the files"
    if tree.empty:
        return "placeholder", "the repository is empty"
    code = [p for p in tree.paths if p.lower().endswith(CODE_EXTENSIONS)]
    says_soon = bool(readme and PLACEHOLDER_PHRASES.search(readme))
    if not code:
        others = [
            p
            for p in tree.paths
            if not re.match(r"(?i)(readme|license|\.git)", p.rsplit("/", 1)[-1])
        ]
        reason = "only a README/LICENSE" if not others else "no source files"
        return "placeholder", reason + ("; README says code is coming" if says_soon else "")
    if says_soon and len(code) <= 3 and not tree.truncated:
        return "placeholder", f"only {len(code)} source file(s) and the README says code is coming"
    return "ok", None


def rank_key(c: RepoCandidate) -> tuple[Any, ...]:
    recency = c.last_commit.timestamp() if c.last_commit else 0.0
    return (
        c.status == "dead",
        OFFICIAL_RANK[c.official],
        c.status != "ok",
        SOURCE_RANK[c.source],
        not c.cites_paper,
        -(c.stars or 0),
        -recency,
    )


def summarize(candidates: list[RepoCandidate]) -> RepoStatus:
    usable = [c for c in candidates if c.status == "ok"]
    official = [c for c in usable if c.official]
    if official:
        return RepoStatus(
            kind="official_found",
            message=f"Official code found: {official[0].owner}/{official[0].name}",
        )
    placeholders = [c for c in candidates if c.status == "placeholder" and c.official is not False]
    if usable:
        n = len(usable)
        noun = "implementation" if n == 1 else "implementations"
        prefix = "No official code"
        if placeholders:
            p = placeholders[0]
            prefix = f"Official code isn't released yet ({p.owner}/{p.name} is a placeholder)"
        return RepoStatus(kind="unofficial_only", message=f"{prefix}. {n} community {noun} found")
    if placeholders:
        p = placeholders[0]
        return RepoStatus(
            kind="placeholder_only",
            message=f"Code announced but not released yet ({p.owner}/{p.name} is a placeholder)",
        )
    return RepoStatus(kind="none", message="No code found for this paper")


# -- the finder ---------------------------------------------------------------------------


class RepoFinder:
    def __init__(self, http: PoliteClient, github: GitHubClient | None = None) -> None:
        self.http = http
        self.github = github or GitHubClient(http)
        self.gitlab = GitLabClient(http)

    async def find(self, doc: PaperDoc) -> FinderResult:
        warnings: list[str] = []
        leads: dict[str, _Lead] = {}

        def add(lead: _Lead) -> None:
            existing = leads.get(lead.ref.key)
            if existing is None:
                leads[lead.ref.key] = lead
                return
            # Keep the most confident source; upgrade officialness; remember extra evidence.
            if SOURCE_RANK[lead.source] < SOURCE_RANK[existing.source]:
                lead.notes += [existing.evidence, *existing.notes]
                lead.search_info = lead.search_info or existing.search_info
                leads[lead.ref.key] = lead
                existing = lead
            elif lead.evidence not in existing.notes and lead.evidence != existing.evidence:
                existing.notes.append(lead.evidence)
            if lead.official and not existing.official:
                existing.official, existing.evidence = True, lead.evidence
            existing.search_info = existing.search_info or lead.search_info

        # 1. links in the paper, then project pages
        page_urls: list[str] = []
        for url in [*doc.links_found, *find_repo_urls(doc.abstract)]:
            ref = parse_repo_url(url)
            if ref is not None:
                official, evidence = release_evidence(doc, ref)
                add(_Lead(ref, "paper_link", official, evidence))
            elif is_project_page(url) and url not in page_urls:
                page_urls.append(url)

        # 2. Hugging Face Papers
        if doc.ref.arxiv_id:
            hf_url, hf_page = await self._hugging_face(doc.ref.arxiv_id, warnings)
            if hf_page and hf_page not in page_urls:
                page_urls.append(hf_page)
            if hf_url and (ref := parse_repo_url(hf_url)):
                add(_Lead(ref, "hf_papers", None, "listed on Hugging Face Papers"))

        for page in page_urls[:MAX_PROJECT_PAGES]:
            for ref in await self._project_page_repos(page):
                add(_Lead(ref, "paper_link", True, f"linked from the project page ({page})"))

        # 3. GitHub search (community implementations)
        try:
            found: list[RepoInfo] = []
            if doc.ref.arxiv_id:
                found = await self.github.search_readmes(doc.ref.arxiv_id, MAX_SEARCH_RESULTS)
            if len(found) < 3 and doc.title:
                found += await self.github.search_readmes(doc.title, MAX_SEARCH_RESULTS)
            for info in found:
                ref = RepoRef("github", info.owner, info.name)
                add(_Lead(ref, "github_search", False, "found by GitHub search", search_info=info))
        except GitHubRateLimited as exc:
            warnings.append(str(exc))

        # Validate the most promising leads first.
        ordered = sorted(
            leads.values(),
            key=lambda lead: (
                SOURCE_RANK[lead.source],
                OFFICIAL_RANK[lead.official],
                -(lead.search_info.stars if lead.search_info else 0),
            ),
        )
        candidates: list[RepoCandidate] = []
        seen: set[str] = set()
        searched = 0
        for lead in ordered[:MAX_VALIDATE]:
            check_owner = lead.source != "github_search" or searched < MAX_OWNER_LOOKUPS_FROM_SEARCH
            searched += lead.source == "github_search"
            try:
                cand = await self._validate(lead, doc, check_owner=check_owner)
            except GitHubRateLimited as exc:
                if str(exc) not in warnings:
                    warnings.append(str(exc))
                cand = self._unvalidated(lead)
            except FetchError as exc:
                warnings.append(str(exc))
                cand = self._unvalidated(lead)
            if cand.id.lower() in seen:  # two leads resolved to the same renamed repo
                continue
            seen.add(cand.id.lower())
            candidates.append(cand)

        candidates.sort(key=rank_key)
        return FinderResult(candidates=candidates, status=summarize(candidates), warnings=warnings)

    # -- sources --------------------------------------------------------------------------

    async def _hugging_face(
        self, arxiv_id: str, warnings: list[str]
    ) -> tuple[str | None, str | None]:
        try:
            result = await self.http.get(HF_PAPERS.format(arxiv_id=arxiv_id), ttl=24 * 3600.0)
        except FetchError as exc:
            warnings.append(str(exc))
            return None, None
        if result.status != 200:
            return None, None
        try:
            body: dict[str, Any] = json.loads(result.content)
        except json.JSONDecodeError:
            return None, None
        repo = body.get("githubRepo")
        page = body.get("projectPage")
        return (str(repo) if repo else None), (str(page) if page else None)

    async def _project_page_repos(self, url: str) -> list[RepoRef]:
        try:
            result = await self.http.get(url, ttl=24 * 3600.0)
        except FetchError:
            return []
        if result.status != 200 or "html" not in (result.content_type or "html"):
            return []
        soup = BeautifulSoup(result.content[:2_000_000], "lxml")
        refs: list[RepoRef] = []
        for a in soup.find_all("a"):
            href = a.get("href")
            ref = parse_repo_url(href) if isinstance(href, str) else None
            if ref is not None and ref.key not in {r.key for r in refs}:
                refs.append(ref)
        return refs[:3]

    # -- validation -----------------------------------------------------------------------

    def _unvalidated(self, lead: _Lead) -> RepoCandidate:
        return RepoCandidate(
            id=lead.ref.id,
            url=lead.ref.url,
            owner=lead.ref.owner,
            name=lead.ref.name,
            source=lead.source,
            official=lead.official,
            official_evidence=lead.evidence,
            status="unknown",
            status_reason="couldn't check this repository right now",
            host=lead.ref.host,
        )

    async def _validate(
        self, lead: _Lead, doc: PaperDoc, *, check_owner: bool = True
    ) -> RepoCandidate:
        if lead.ref.host == "gitlab":
            return await self._validate_gitlab(lead)
        info = lead.search_info or await self.github.repo(lead.ref.owner, lead.ref.name)
        if info is None:
            cand = self._unvalidated(lead)
            return cand.model_copy(
                update={"status": "dead", "status_reason": "repository not found"}
            )

        tree = await self.github.tree(info)
        readme = await self.github.readme(info) if lead.search_info is None else None
        status, reason = health(tree, readme)
        arxiv_id = doc.ref.arxiv_id
        cites = lead.search_info is not None or bool(arxiv_id and readme and arxiv_id in readme)

        official, evidence = lead.official, lead.evidence
        if official is not True and check_owner:
            display = None
            if info.owner_type == "User":
                display = await self.github.user_name(info.owner)
            author = owner_matches_author(info.owner, display, doc.authors)
            if author:
                official, evidence = (
                    True,
                    f"likely official: owner {info.owner} matches author {author}",
                )
        if info.fork and official is not True:
            reason = (reason + "; " if reason else "") + "a fork"
        renamed = info.full_name.lower() != lead.ref.full_name.lower()
        if renamed:
            evidence += f" (moved from {lead.ref.full_name})"
        return RepoCandidate(
            id=f"github:{info.full_name}",
            url=info.html_url,
            owner=info.owner,
            name=info.name,
            source=lead.source,
            official=official,
            official_evidence=evidence,
            status=status,
            status_reason=reason,
            stars=info.stars,
            last_commit=info.pushed_at,
            language=info.language,
            host="github",
            default_branch=info.default_branch,
            cites_paper=cites,
        )

    async def _validate_gitlab(self, lead: _Lead) -> RepoCandidate:
        found = await self.gitlab.repo(lead.ref.full_name)
        if found is None:
            cand = self._unvalidated(lead)
            return cand.model_copy(
                update={"status": "dead", "status_reason": "repository not found"}
            )
        project_id, info = found
        status, reason = health(await self.gitlab.tree(project_id), None)
        return RepoCandidate(
            id=f"gitlab:{info.full_name}",
            url=info.html_url,
            owner=info.owner,
            name=info.name,
            source=lead.source,
            official=lead.official,
            official_evidence=lead.evidence,
            status=status,
            status_reason=reason,
            stars=info.stars,
            last_commit=info.pushed_at,
            host="gitlab",
            default_branch=info.default_branch,
        )
