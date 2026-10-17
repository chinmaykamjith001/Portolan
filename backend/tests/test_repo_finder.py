from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from portolan.ingest.http import PoliteClient
from portolan.models.repo import RepoCandidate
from portolan.repo.finder import RepoFinder, health, owner_matches_author, rank_key, summarize
from portolan.repo.github import GitHubClient, Tree
from portolan.repo.links import parse_repo_url, release_evidence
from portolan.store.db import Database
from tests.conftest import FakeClock
from tests.repo_helpers import GH, make_doc, mock_hf, mock_repo, mock_search, repo_json


@pytest.fixture
async def http(db: Database) -> AsyncIterator[PoliteClient]:
    client = PoliteClient(db, clock=FakeClock())
    yield client
    await client.aclose()


@pytest.fixture
def finder(http: PoliteClient) -> RepoFinder:
    return RepoFinder(http, GitHubClient(http, token=""))


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


# -- pure helpers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/microsoft/LoRA", "github:microsoft/LoRA"),
        ("https://github.com/microsoft/LoRA.git", "github:microsoft/LoRA"),
        ("http://www.github.com/a/b/tree/main/src", "github:a/b"),
        (
            "github.com/harvardnlp/annotated-transformer).",
            "github:harvardnlp/annotated-transformer",
        ),
        ("https://github.com/microsoft", None),
        ("https://github.com/features/copilot", None),
        ("https://github.com/orgs/acme/repositories", None),
        ("https://gist.github.com/a/b", None),
        ("https://gitlab.com/group/sub/proj/-/tree/main", "gitlab:group/sub/proj"),
        ("https://gitlab.com/explore/projects", None),
        ("https://example.org/code", None),
    ],
)
def test_parse_repo_url(url: str, expected: str | None) -> None:
    ref = parse_repo_url(url)
    assert (ref.id if ref else None) == expected


def test_release_evidence_needs_context() -> None:
    ref = parse_repo_url("https://github.com/acme/toy")
    other = parse_repo_url("https://github.com/pytorch/fairseq")
    assert ref is not None and other is not None
    in_abstract = make_doc(abstract="Code: https://github.com/acme/toy")
    assert release_evidence(in_abstract, ref)[0] is True
    released = make_doc(paragraphs=["Our code is available at https://github.com/acme/toy."])
    assert release_evidence(released, ref) == (True, "linked in the paper as its code (§1 ¶1)")
    baseline = make_doc(paragraphs=["We train with fairseq (https://github.com/pytorch/fairseq)."])
    official, evidence = release_evidence(baseline, other)
    assert official is None and "may be a third-party tool" in evidence


@pytest.mark.parametrize(
    ("login", "display", "match"),
    [
        ("edwardjhu", None, "Edward Hu"),
        ("someone", "Edward J. Hu", "Edward Hu"),
        ("bsample", None, "Bo Sample"),
        ("sample-bo", None, None),  # family name too short to trust on its own
        ("vaswani-lab", None, None),
        ("microsoft", None, None),
        ("adaexample", None, "Ada Example"),
        ("hu", "Yelong Hu", None),  # same family name, different first initial
    ],
)
def test_owner_matches_author(login: str, display: str | None, match: str | None) -> None:
    authors = ["Edward Hu", "Bo Sample", "Ada Example"]
    assert owner_matches_author(login, display, authors) == match


def test_health() -> None:
    assert health(Tree(["README.md", "src/model.py"], False), None) == ("ok", None)
    assert health(Tree([], False, empty=True), None) == ("placeholder", "the repository is empty")
    status, reason = health(Tree(["README.md", "LICENSE"], False), "Code coming soon!")
    assert status == "placeholder" and reason == "only a README/LICENSE; README says code is coming"
    status, reason = health(Tree(["README.md", "setup.py"], False), "Code will be released.")
    assert status == "placeholder" and "README says code is coming" in (reason or "")
    many = [f"m{i}.py" for i in range(10)]
    assert health(Tree(many, False), "Pretrained weights coming soon")[0] == "ok"
    assert health(None, None)[0] == "unknown"


def cand(name: str, **kw: object) -> RepoCandidate:
    base: dict[str, object] = {
        "id": f"github:x/{name}", "url": "u", "owner": "x", "name": name,
        "source": "github_search", "official": False, "official_evidence": None, "status": "ok",
    }  # fmt: skip
    return RepoCandidate.model_validate({**base, **kw})


def test_ranking_and_summary() -> None:
    ranked = sorted(
        [
            cand("popular", stars=5000),
            cand("dead-official", official=True, status="dead"),
            cand("official", official=True, source="paper_link", stars=3),
            cand("unknown", official=None, source="hf_papers"),
            cand("cites", stars=10, cites_paper=True),
        ],
        key=rank_key,
    )
    assert [c.name for c in ranked] == ["official", "unknown", "cites", "popular", "dead-official"]
    assert summarize(ranked).message == "Official code found: x/official"
    assert summarize([cand("a"), cand("b")]).message == (
        "No official code. 2 community implementations found"
    )
    mixed = [cand("soon", official=True, status="placeholder"), cand("impl")]
    assert summarize(mixed).message == (
        "Official code isn't released yet (x/soon is a placeholder). "
        "1 community implementation found"
    )
    assert summarize([cand("soon", official=True, status="placeholder")]).kind == "placeholder_only"
    assert summarize([cand("gone", status="dead")]).message == "No code found for this paper"
    assert summarize([]).kind == "none"


# -- finder scenarios (mocked HTTP) -------------------------------------------------------


async def test_official_repo_linked_in_paper(finder: RepoFinder, mock: respx.MockRouter) -> None:
    doc = make_doc(
        abstract="We release code at https://github.com/acme/toy.",
        links=["https://github.com/acme/toy"],
    )
    mock_hf(mock)
    mock_repo(mock, "acme/toy", stars=120, owner_type="Organization")
    mock_repo(mock, "fan/toy-pytorch", stars=40)
    mock_search(mock, repo_json("acme/toy", stars=120), repo_json("fan/toy-pytorch", stars=40))
    result = await finder.find(doc)
    assert result.status.kind == "official_found"
    assert result.status.message == "Official code found: acme/toy"
    first, second = result.candidates
    assert (first.id, first.official, first.source, first.status) == (
        "github:acme/toy", True, "paper_link", "ok",
    )  # fmt: skip
    assert first.official_evidence == "linked in the paper's abstract"
    assert first.cites_paper and first.stars == 120
    assert (second.id, second.official, second.source) == (
        "github:fan/toy-pytorch",
        False,
        "github_search",
    )
    assert result.best is first


async def test_unofficial_only(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock_repo(mock, "fan/one", stars=40)
    mock_repo(mock, "fan2/two", stars=90)
    mock_search(mock, repo_json("fan/one", stars=40), repo_json("fan2/two", stars=90))
    result = await finder.find(make_doc())
    assert result.status.kind == "unofficial_only"
    assert result.status.message == "No official code. 2 community implementations found"
    assert [c.name for c in result.candidates] == ["two", "one"]  # by stars


async def test_placeholder_repo(finder: RepoFinder, mock: respx.MockRouter) -> None:
    doc = make_doc(abstract="Code will be at https://github.com/acme/soon")
    mock_hf(mock)
    mock_repo(mock, "acme/soon", files=["README.md", "LICENSE"], readme="# Soon\nCode coming soon.")
    mock_search(mock)
    result = await finder.find(doc)
    assert result.status.kind == "placeholder_only"
    assert result.status.message == (
        "Code announced but not released yet (acme/soon is a placeholder)"
    )
    assert result.candidates[0].status_reason == "only a README/LICENSE; README says code is coming"
    assert result.best is None


async def test_empty_repo_is_a_placeholder(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock_repo(mock, "acme/empty", files=[], readme=None)
    mock_search(mock)
    result = await finder.find(make_doc(abstract="Code: github.com/acme/empty"))
    assert result.candidates[0].status == "placeholder"


async def test_dead_link_and_no_code(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock.get(host=GH, path="/repos/acme/gone").mock(return_value=httpx.Response(404))
    mock_search(mock)
    result = await finder.find(make_doc(abstract="Code: https://github.com/acme/gone"))
    assert result.status.kind == "none"
    assert result.status.message == "No code found for this paper"
    gone = result.candidates[0]
    assert gone.status == "dead" and gone.status_reason == "repository not found"


async def test_nothing_anywhere(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock_search(mock)
    result = await finder.find(make_doc())
    assert result.candidates == [] and result.status.kind == "none"


async def test_renamed_repo_is_followed(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock_repo(mock, "newowner/toy", requested_as="oldowner/toy", owner_type="Organization")
    mock_search(mock)
    result = await finder.find(make_doc(abstract="Our code: https://github.com/oldowner/toy"))
    c = result.candidates[0]
    assert c.id == "github:newowner/toy" and c.url == "https://github.com/newowner/toy"
    assert c.official_evidence == "linked in the paper's abstract (moved from oldowner/toy)"


async def test_hugging_face_repo_with_author_owner_is_likely_official(
    finder: RepoFinder, mock: respx.MockRouter
) -> None:
    mock_hf(mock, githubRepo="https://github.com/someone/toy-code", githubRepoAddedBy="auto")
    mock_repo(mock, "someone/toy-code", display_name="Ada B. Example")
    mock_search(mock)
    result = await finder.find(make_doc())
    c = result.candidates[0]
    assert c.source == "hf_papers" and c.official is True
    assert c.official_evidence == "likely official: owner someone matches author Ada Example"
    assert result.status.kind == "official_found"


async def test_hugging_face_repo_without_author_match_stays_unverified(
    finder: RepoFinder, mock: respx.MockRouter
) -> None:
    mock_hf(mock, githubRepo="https://github.com/bigorg/toy")
    mock_repo(mock, "bigorg/toy", owner_type="Organization")
    mock_search(mock)
    result = await finder.find(make_doc())
    c = result.candidates[0]
    assert c.official is None and c.official_evidence == "listed on Hugging Face Papers"
    assert result.status.kind == "unofficial_only"


async def test_third_party_mention_is_not_official(
    finder: RepoFinder, mock: respx.MockRouter
) -> None:
    doc = make_doc(
        paragraphs=["We train with fairseq (https://github.com/pytorch/fairseq)."],
        links=["https://github.com/pytorch/fairseq"],
    )
    mock_hf(mock)
    mock_repo(mock, "pytorch/fairseq", owner_type="Organization", stars=30000)
    mock_search(mock)
    result = await finder.find(doc)
    assert result.candidates[0].official is None
    assert result.status.kind != "official_found"


async def test_project_page_links(finder: RepoFinder, mock: respx.MockRouter) -> None:
    doc = make_doc(links=["https://acme.github.io/toy/"])
    mock_hf(mock)
    mock.get("https://acme.github.io/toy/").mock(
        return_value=httpx.Response(
            200,
            html='<a href="https://github.com/acme/toy-code">Code</a> <a href="/paper.pdf">PDF</a>',
        )
    )
    mock_repo(mock, "acme/toy-code", owner_type="Organization")
    mock_search(mock)
    result = await finder.find(doc)
    c = result.candidates[0]
    assert c.official is True
    assert c.official_evidence == "linked from the project page (https://acme.github.io/toy/)"


async def test_gitlab_repo(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock_search(mock)
    mock.get(url__regex=r"^https://gitlab.com/api/v4/projects/group(%2F|/)toy$").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 7,
                "path": "toy",
                "path_with_namespace": "group/toy",
                "star_count": 5,
                "web_url": "https://gitlab.com/group/toy",
                "default_branch": "main",
                "last_activity_at": "2024-02-01T00:00:00Z",
                "namespace": {"kind": "group"},
            },
        )
    )
    mock.get(host="gitlab.com", path="/api/v4/projects/7/repository/tree").mock(
        return_value=httpx.Response(200, json=[{"path": "toy.py", "type": "blob"}])
    )
    result = await finder.find(make_doc(abstract="Our code: https://gitlab.com/group/toy"))
    c = result.candidates[0]
    assert (c.id, c.host, c.status, c.official) == ("gitlab:group/toy", "gitlab", "ok", True)


async def test_rate_limit_is_reported_not_fatal(finder: RepoFinder, mock: respx.MockRouter) -> None:
    mock_hf(mock)
    mock.get(host=GH, path="/search/repositories").mock(
        return_value=httpx.Response(
            403, headers={"x-ratelimit-remaining": "0"}, json={"message": "API rate limit exceeded"}
        )
    )
    mock.get(host=GH, path="/repos/acme/toy").mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
    )
    result = await finder.find(make_doc(abstract="Code: https://github.com/acme/toy"))
    assert any("GITHUB_TOKEN" in w for w in result.warnings)
    assert len(result.warnings) == 1
    c = result.candidates[0]
    assert c.status == "unknown" and c.official is True


async def test_token_is_sent_only_as_a_header(http: PoliteClient, mock: respx.MockRouter) -> None:
    route = mock.get(host=GH, path="/repos/acme/toy").mock(
        return_value=httpx.Response(200, json=repo_json("acme/toy"))
    )
    await GitHubClient(http, token="ghp_test_value").repo("acme", "toy")
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer ghp_test_value"
    assert "ghp_test_value" not in str(request.url)
