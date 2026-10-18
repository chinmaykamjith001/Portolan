"""Code grounding (spec §10.7.2): validation of line notes against the stored index."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from portolan.code.index_store import IndexStore, split_lines
from portolan.config import Config, ModelsConfig
from portolan.detail.explain import DetailError
from portolan.detail.ground import (
    NO_REPO,
    NOT_IN_CODE,
    NOT_INDEXED,
    UNVERIFIED,
    blob_links,
    ground_code,
)
from portolan.llm.gateway import Gateway
from portolan.llm.providers.base import CallContext
from portolan.llm.types import Message, ProviderResponse, Usage
from portolan.models.concept import Concept
from portolan.models.repo import RepoCandidate
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.repos import choose_repo
from tests.repo_helpers import make_doc
from tests.test_code_store import REPO, SHA, attention_files, save_files

ATTN = "model/attention.py"  # forward() is lines 5-8: def, docstring, q = ..., return softmax(...)


class ScriptedGround:
    """Replies with the given GroundOut-shaped dicts in order; records every prompt."""

    name = "fake"
    uses_quota = False

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(
        self, *, model: str, messages: list[Message], temperature: float,
        schema: type[BaseModel] | None, ctx: CallContext,
    ) -> ProviderResponse:  # fmt: skip
        assert ctx.template_id == "ground_code"
        self.prompts.append(messages[-1].content)
        return ProviderResponse(text=json.dumps(self.replies.pop(0)), usage=Usage(), model="fake")


def reply(
    spans: list[tuple[str, int, int]] | None = None,
    notes: list[tuple[int, str]] | None = None,
    *,
    grounding: str = "in_code",
    libs: list[str] | None = None,
    summary: str = "Lines 5-8 implement attention.",
) -> dict[str, Any]:
    return {
        "grounding": grounding,
        "spans": [{"path": p, "start_line": a, "end_line": b} for p, a, b in spans or []],
        "notes": [
            {"line": n, "tag": "§3.2", "note": text, "kind": "implements"}
            for n, text in notes or []
        ],
        "library_calls": libs or [],
        "summary": summary,
    }


GOOD = reply([(ATTN, 5, 8)], [(7, "Projects the input to queries."), (8, "Scores and softmaxes.")])


@pytest.fixture
def chart(db: Database) -> ChartStore:
    store = ChartStore(db)
    store.upsert_concept(
        "p1",
        Concept(
            id="multi-head-attention",
            canonical_name="multi head attention",
            title="Multi-head attention",
            gist="Project, attend and concatenate several heads.",
            depth=1,
        ),
    )
    return store


def repo(host: str = "github") -> RepoCandidate:
    return RepoCandidate(
        id=REPO,
        url="https://github.com/example/transformer",
        owner="example",
        name="transformer",
        source="paper_link",
        official=True,
        official_evidence=None,
        status="ok",
        host=host,  # type: ignore[arg-type]
    )


@pytest.fixture
def indexed(db: Database) -> None:
    choose_repo(db, "p1", repo())
    save_files(IndexStore(db), attention_files())


def gateway(db: Database, provider: ScriptedGround) -> Gateway:
    config = Config()
    config.llm.provider = "mock"
    config.llm.models = ModelsConfig(fast="f", default="d", strong="s")
    return Gateway(config, db, provider=provider)


async def run(db: Database, chart: ChartStore, provider: ScriptedGround) -> Any:
    doc = make_doc().model_copy(update={"paper_id": "p1"})
    return await ground_code(doc, gateway(db, provider), chart, db, "multi-head-attention")


# -- the happy path ---------------------------------------------------------------------------


async def test_valid_notes_are_kept_with_code_and_links(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    long_note = " ".join(f"w{i}" for i in range(40))
    provider = ScriptedGround(
        reply(
            [(ATTN, 5, 8)],
            [(8, "Scores and softmaxes."), (7, long_note)],  # out of order, one too long
            libs=["torch.softmax"],
        )
    )
    g = await run(db, chart, provider)
    assert g.grounding == "in_code" and g.concept_id == "multi-head-attention"
    assert [n.line for n in g.notes] == [7, 8]  # sorted
    assert g.notes[0].note.endswith("…") and len(g.notes[0].note.split()) == 25
    assert [(s.path, s.start_line, s.end_line) for s in g.spans] == [(ATTN, 5, 8)]
    assert g.spans[0].repo_id == REPO and g.spans[0].commit_sha == SHA
    assert g.code[ATTN][7] == "        q = self.w_q(x)"
    assert sorted(g.code[ATTN]) == [5, 6, 7, 8]
    assert g.links == {ATTN: f"https://github.com/example/transformer/blob/{SHA}/{ATTN}"}
    assert g.library_calls == ["torch.softmax"]
    assert g.summary == "Lines 5-8 implement attention."
    assert len(provider.prompts) == 1
    assert "    7|         q = self.w_q(x)" in provider.prompts[0]  # candidates are numbered


async def test_spans_are_clamped_and_paths_normalized(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    g = await run(
        db, chart, ScriptedGround(reply([("./" + ATTN, 0, 9999)], [(7, "Projects the input.")]))
    )
    assert [(s.path, s.start_line) for s in g.spans] == [(ATTN, 1)]
    text = IndexStore(db).file_text(REPO, SHA, ATTN) or ""
    assert g.spans[0].end_line == len(split_lines(text))  # clamped to the file
    assert g.spans[0].end_line - g.spans[0].start_line + 1 <= 80


async def test_note_outside_every_span_is_dropped(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    provider = ScriptedGround(
        reply([(ATTN, 5, 8)], [(7, "Projects the input."), (40, "Invented line."), (1, "Before.")])
    )
    g = await run(db, chart, provider)
    assert [n.line for n in g.notes] == [7]
    assert len(provider.prompts) == 1  # something survived: no retry


# -- failure and fallback ---------------------------------------------------------------------


async def test_retry_with_reasons_then_success(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    provider = ScriptedGround(reply([("nope.py", 1, 5)], [(2, "Made up.")]), GOOD)
    g = await run(db, chart, provider)
    assert g.grounding == "in_code" and len(g.notes) == 2
    assert len(provider.prompts) == 2
    assert "previous answer was refused" in provider.prompts[1]
    assert "'nope.py' is not in the repository" in provider.prompts[1]
    assert "refused" not in provider.prompts[0]


async def test_two_failures_fall_back_to_concept_only(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    bad = reply([(ATTN, 5, 8)], [(99, "Nowhere.")])
    provider = ScriptedGround(bad, bad)
    g = await run(db, chart, provider)
    assert g.grounding == "concept_only" and g.summary == UNVERIFIED
    assert g.spans == [] and g.notes == [] and g.code == {} and g.links == {}
    assert len(provider.prompts) == 2  # exactly one retry


async def test_model_saying_concept_only_is_trusted(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    provider = ScriptedGround(reply(grounding="concept_only", summary="Not spelled out in code."))
    g = await run(db, chart, provider)
    assert (g.grounding, g.summary) == ("concept_only", "Not spelled out in code.")
    assert len(provider.prompts) == 1


async def test_library_only(db: Database, chart: ChartStore, indexed: None) -> None:
    provider = ScriptedGround(
        reply(grounding="in_library", libs=["torch.softmax", "torch.softmax", "not dotted"],
              summary="Done by torch.softmax.")
    )  # fmt: skip
    g = await run(db, chart, provider)
    assert g.grounding == "in_library" and g.spans == [] and g.notes == []
    assert g.library_calls == ["torch.softmax"]
    assert g.summary == "Done by torch.softmax."
    assert len(provider.prompts) == 1


async def test_library_call_from_an_unimported_library_is_refused(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    only_numpy = reply(grounding="in_library", libs=["numpy.exp"])
    provider = ScriptedGround(only_numpy, only_numpy)
    g = await run(db, chart, provider)
    assert g.grounding == "concept_only"
    assert "does not come from a library the repository imports" in provider.prompts[1]


async def test_spans_without_valid_notes_never_become_in_code(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    g = await run(db, chart, ScriptedGround(*[reply([(ATTN, 5, 8)], libs=["torch.softmax"])] * 2))
    assert g.grounding == "in_library"  # no verifiable note, but the library call stands
    assert g.spans == []


# -- nothing to ground against ----------------------------------------------------------------


async def test_no_repo(db: Database, chart: ChartStore) -> None:
    provider = ScriptedGround()
    g = await run(db, chart, provider)
    assert (g.grounding, g.summary) == ("concept_only", NO_REPO)
    assert provider.prompts == []


async def test_repo_not_indexed(db: Database, chart: ChartStore) -> None:
    choose_repo(db, "p1", repo())
    provider = ScriptedGround()
    g = await run(db, chart, provider)
    assert (g.grounding, g.summary) == ("concept_only", NOT_INDEXED)
    assert provider.prompts == []


async def test_nothing_retrieved_costs_no_request(db: Database, chart: ChartStore) -> None:
    choose_repo(db, "p1", repo())
    save_files(IndexStore(db), attention_files())
    chart.upsert_concept(
        "p1",
        Concept(
            id="zebra", canonical_name="zebra", title="Zebra", gist="Striped quadruped.", depth=1
        ),
    )
    provider = ScriptedGround()
    doc = make_doc().model_copy(update={"paper_id": "p1"})
    g = await ground_code(doc, gateway(db, provider), chart, db, "zebra")
    assert (g.grounding, g.summary) == ("concept_only", NOT_IN_CODE)
    assert provider.prompts == []


async def test_newest_index_of_the_repo_is_used(
    db: Database, chart: ChartStore, indexed: None
) -> None:
    """A second commit indexed later wins, and its sha ends up on the spans."""
    new_sha = "b" * 40
    save_files(IndexStore(db), attention_files(), sha=new_sha)
    db.execute("UPDATE code_indexes SET created_at = '2000-01-01' WHERE commit_sha = ?", (SHA,))
    g = await run(db, chart, ScriptedGround(GOOD))
    assert {s.commit_sha for s in g.spans} == {new_sha}


async def test_unknown_concept(db: Database, chart: ChartStore) -> None:
    doc = make_doc().model_copy(update={"paper_id": "p1"})
    with pytest.raises(DetailError):
        await ground_code(doc, gateway(db, ScriptedGround()), chart, db, "nope")


# -- links ------------------------------------------------------------------------------------


def test_blob_links() -> None:
    gh, gl = (
        repo(),
        repo().model_copy(update={"host": "gitlab", "url": "https://gitlab.com/g/s/r.git"}),
    )
    assert blob_links(gh, SHA, ["a.py"]) == {"a.py": f"{gh.url}/blob/{SHA}/a.py"}
    assert blob_links(gl, SHA, ["a.py"]) == {"a.py": f"https://gitlab.com/g/s/r/-/blob/{SHA}/a.py"}
    assert blob_links(gh.model_copy(update={"host": "upload"}), SHA, ["a.py"]) == {}
    assert blob_links(gh, "deadbeef", ["a.py"]) == {}  # a content hash is not a commit
    assert blob_links(gh.model_copy(update={"url": "http://github.com/x/y"}), SHA, ["a.py"]) == {}
