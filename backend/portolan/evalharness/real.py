"""The real engine: Portolan's own pipeline end to end, one dataset record at a time.

paper -> resolve + ingest (`ingest_ref`) -> repo from the record's URL -> clone + index
(`ensure_index`) -> `verify_paper` -> the findings it saved. Nothing here is evaluation-specific
logic about the paper or code, so the numbers measure the product, not a special path.

Known gap: `clone` has no commit pinning, so the repository is indexed at its default branch HEAD.
When the record names a different commit the result carries a note, and the report lists it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from portolan.code.ensure import ensure_index
from portolan.config import Config
from portolan.evalharness.records import EvalRecord
from portolan.evalharness.runner import EngineOutput, EvalError, engine_fingerprint
from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import IngestReport, ingest_ref
from portolan.ingest.resolver import ResolveResult, resolve
from portolan.llm.gateway import Gateway
from portolan.models.finding import Finding
from portolan.models.paper import PaperDoc, PaperRef
from portolan.models.repo import RepoCandidate
from portolan.repo.links import parse_repo_url
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.findings import FindingStore
from portolan.store.repos import choose_repo
from portolan.verify.engine import verify_paper

ResolveFn = Callable[[str, PoliteClient], Awaitable[ResolveResult]]
IngestFn = Callable[[PaperRef, PoliteClient, Database | None], Awaitable[IngestReport]]
IndexFn = Callable[[Config, Database, str, RepoCandidate], Awaitable[bool]]
VerifyFn = Callable[..., Awaitable[None]]


def candidate_from_url(url: str) -> RepoCandidate:
    ref = parse_repo_url(url)
    if ref is None:
        raise EvalError(f"{url} isn't a GitHub or GitLab repository address.")
    return RepoCandidate(
        id=ref.id,
        url=ref.url,
        owner=ref.owner,
        name=ref.name,
        source="user",
        official=None,
        official_evidence="named by the evaluation dataset",
        status="ok",
        host=ref.host,
    )


def _ignore(_finding: Finding) -> None:
    return None


class RealEngine:
    def __init__(
        self,
        config: Config,
        db: Database,
        gateway: Gateway,
        client: PoliteClient,
        *,
        count_requests: Callable[[], int] | None = None,
        resolve_fn: ResolveFn = resolve,
        ingest_fn: IngestFn | None = None,
        index_fn: IndexFn = ensure_index,
        verify_fn: VerifyFn = verify_paper,
    ) -> None:
        self.config, self.db, self.gateway, self.client = config, db, gateway, client
        self.version = engine_fingerprint()
        self._count = count_requests or self._gateway_requests
        self._resolve = resolve_fn
        self._ingest = ingest_fn or self._default_ingest
        self._index = index_fn
        self._verify = verify_fn

    def _gateway_requests(self) -> int:
        return sum(int(r["requests"]) for r in self.gateway.limiter.budget.today())

    async def _default_ingest(
        self, ref: PaperRef, client: PoliteClient, db: Database | None
    ) -> IngestReport:
        return await ingest_ref(ref, client, db)

    async def check(self, record: EvalRecord) -> EngineOutput:
        doc = await self._paper(record)
        candidate = candidate_from_url(record.repo_url)
        choose_repo(self.db, doc.paper_id, candidate)
        if not await self._index(self.config, self.db, doc.paper_id, candidate):
            raise EvalError(f"Couldn't download the code from {candidate.url}.")
        note = self._commit_note(candidate, record.repo_commit)

        findings_store = FindingStore(self.db)
        findings_store.delete_paper(doc.paper_id)  # a clean run; LLM answers still come from cache
        before = self._count()
        await self._verify(doc, self.gateway, ChartStore(self.db), self.db, _ignore)
        findings: list[Finding] = findings_store.all(doc.paper_id)
        return EngineOutput(findings=findings, requests_used=self._count() - before, note=note)

    async def _paper(self, record: EvalRecord) -> PaperDoc:
        resolved = await self._resolve(record.paper, self.client)
        if resolved.error:
            raise EvalError(resolved.error)
        if resolved.ref is None:
            raise EvalError(
                f"“{record.paper}” matches several papers; the dataset should give an arXiv id."
            )
        return (await self._ingest(resolved.ref, self.client, self.db)).doc

    def _commit_note(self, candidate: RepoCandidate, wanted: str | None) -> str | None:
        row = self.db.fetchone(
            "SELECT commit_sha FROM code_indexes WHERE repo_id = ? ORDER BY rowid DESC LIMIT 1",
            (candidate.id,),
        )
        indexed = str(row["commit_sha"]) if row else None
        if wanted and indexed and not (indexed.startswith(wanted) or wanted.startswith(indexed)):
            return (
                f"dataset commit {wanted[:12]}, indexed {indexed[:12]} (HEAD): results may differ"
            )
        return None
