from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from portolan.ingest.arxiv import API_URL, parse_feed
from portolan.ingest.http import FetchError, PoliteClient, user_agent
from portolan.ingest.pipeline import (
    IngestError,
    extract_source,
    fetch_and_parse,
    ingest_ref,
    parse_upload,
)
from portolan.ingest.resolver import parse_arxiv, parse_doi, resolve
from portolan.models.paper import PaperRef
from portolan.store.db import Database
from tests.conftest import FakeClock
from tests.ingest_helpers import FIXTURES, gz_single_tex, latex_tarball, synthetic_pdf


def atom(*entries: tuple[str, str, list[str]]) -> bytes:
    body = "".join(
        f"<entry><id>http://arxiv.org/abs/{eid}</id><title>{title}</title>"
        + "".join(f"<author><name>{a}</name></author>" for a in authors)
        + "<summary>An abstract.</summary><published>2021-06-17T00:00:00Z</published></entry>"
        for eid, title, authors in entries
    )
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def client(db: Database, clock: FakeClock) -> AsyncIterator[PoliteClient]:
    c = PoliteClient(db, clock=clock, contact="test@example.org")
    yield c
    await c.aclose()


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


# -- resolver: pure parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1706.03762", ("1706.03762", None)),
        ("arXiv:1706.03762v7", ("1706.03762", "v7")),
        ("https://arxiv.org/abs/1706.03762", ("1706.03762", None)),
        ("https://arxiv.org/abs/1706.03762v5", ("1706.03762", "v5")),
        ("http://arxiv.org/pdf/2106.09685v2.pdf", ("2106.09685", "v2")),
        ("https://arxiv.org/pdf/2106.09685", ("2106.09685", None)),
        ("https://arxiv.org/html/2106.09685v2#S4", ("2106.09685", "v2")),
        ("https://export.arxiv.org/abs/2106.09685", ("2106.09685", None)),
        ("https://ar5iv.labs.arxiv.org/html/1706.03762", ("1706.03762", None)),
        ("https://ar5iv.org/abs/1706.03762", ("1706.03762", None)),
        ("https://www.alphaxiv.org/abs/2106.09685", ("2106.09685", None)),
        ("https://www.alphaxiv.org/overview/2106.09685v1", ("2106.09685", "v1")),
        ("https://huggingface.co/papers/2106.09685", ("2106.09685", None)),
        ("hep-th/9901001v2", ("hep-th/9901001", "v2")),
        ("https://arxiv.org/abs/math.GT/0309136", ("math.GT/0309136", None)),
        ("  2106.09685  ", ("2106.09685", None)),
        ("Attention is all you need", None),
        ("10.1145/3292500.3330701", None),
    ],
)
def test_parse_arxiv(raw: str, expected: tuple[str, str | None] | None) -> None:
    assert parse_arxiv(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1145/3292500.3330701", "10.1145/3292500.3330701"),
        ("doi:10.1038/nature14539", "10.1038/nature14539"),
        ("https://doi.org/10.48550/arXiv.1706.03762", "10.48550/arXiv.1706.03762"),
        ("Attention is all you need", None),
    ],
)
def test_parse_doi(raw: str, expected: str | None) -> None:
    assert parse_doi(raw) == expected


def test_parse_feed_ignores_entities_and_error_entries() -> None:
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE f [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors</id>'
        b"<title>Error</title></entry><entry><id>http://arxiv.org/abs/2106.09685v2</id>"
        b"<title>&x;LoRA</title></entry></feed>"
    )
    entries = parse_feed(evil)
    assert [(e.arxiv_id, e.version) for e in entries] == [("2106.09685", "v2")]
    assert "root:" not in entries[0].title


# -- resolver: network paths --------------------------------------------------------------


async def test_resolve_arxiv_needs_no_network(client: PoliteClient, mock: respx.MockRouter) -> None:
    result = await resolve("https://arxiv.org/abs/1706.03762v7", client)
    assert result.ref == PaperRef(arxiv_id="1706.03762", version="v7")
    assert client.requests_made == 0


async def test_resolve_arxiv_doi_directly(client: PoliteClient) -> None:
    result = await resolve("https://doi.org/10.48550/arXiv.2106.09685", client)
    assert result.ref is not None and result.ref.arxiv_id == "2106.09685"
    assert client.requests_made == 0


async def test_resolve_doi_via_semantic_scholar(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    route = mock.get(url__startswith="https://api.semanticscholar.org/graph/v1/paper/DOI:").mock(
        return_value=httpx.Response(
            200, json={"title": "Some Paper", "externalIds": {"ArXiv": "1234.56789", "DOI": "x"}}
        )
    )
    result = await resolve("10.1000/xyz123", client)
    assert result.ref == PaperRef(arxiv_id="1234.56789", doi="10.1000/xyz123", title="Some Paper")
    assert route.calls.last.request.url.params["fields"] == "externalIds,title,openAccessPdf"


async def test_resolve_doi_without_arxiv_uses_open_access_pdf(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get(url__startswith="https://api.semanticscholar.org/").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Journal Paper",
                "externalIds": {},
                "openAccessPdf": {"url": "https://example.org/p.pdf"},
            },
        )
    )
    result = await resolve("10.1000/journal", client)
    assert result.ref is not None and result.ref.arxiv_id is None
    assert result.ref.source_kind == "pdf" and result.pdf_url == "https://example.org/p.pdf"


async def test_resolve_unknown_doi(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(url__startswith="https://api.semanticscholar.org/").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    result = await resolve("10.1000/nope", client)
    assert result.ref is None and result.error and "Upload the PDF" in result.error


async def test_resolve_title_returns_candidates_never_a_guess(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    route = mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=atom(
                ("1706.03762v7", "Attention Is All You Need", ["A. Vaswani", "N. Shazeer"]),
                ("2001.00001v1", "Attention Is Not All You Need", ["B. Other"]),
            ),
        )
    )
    result = await resolve("Attention is all you need", client)
    assert result.ref is None and result.needs_confirmation
    assert [c.arxiv_id for c in result.candidates] == ["1706.03762", "2001.00001"]
    assert result.candidates[0].year == "2021"
    assert route.calls.last.request.url.params["search_query"] == 'ti:"Attention is all you need"'


async def test_resolve_title_no_match(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(API_URL).mock(return_value=httpx.Response(200, content=atom()))
    result = await resolve("zzzz qqqq", client)
    assert result.error and "No arXiv paper" in result.error


async def test_resolve_unrecognized_url(client: PoliteClient) -> None:
    result = await resolve("https://example.com/some/page", client)
    assert result.error and "upload the PDF" in result.error


# -- polite client ------------------------------------------------------------------------


async def test_client_spaces_requests_per_host(
    client: PoliteClient, clock: FakeClock, mock: respx.MockRouter
) -> None:
    mock.get(url__startswith="https://arxiv.org/").mock(return_value=httpx.Response(200, text="x"))
    mock.get(url__startswith="https://example.org/").mock(
        return_value=httpx.Response(200, text="y")
    )
    start = clock.now()
    await client.get("https://arxiv.org/a", cache=False)
    await client.get("https://example.org/a", cache=False)  # other host: no wait
    assert clock.now() == start
    await client.get("https://arxiv.org/b", cache=False)
    assert clock.now() - start >= 3.0
    sent = mock.calls[0].request
    assert sent.headers["user-agent"] == user_agent("test@example.org")
    assert "Portolan/" in sent.headers["user-agent"]


async def test_client_caches_200_and_404(client: PoliteClient, mock: respx.MockRouter) -> None:
    ok = mock.get("https://arxiv.org/ok").mock(return_value=httpx.Response(200, content=b"body"))
    missing = mock.get("https://arxiv.org/missing").mock(return_value=httpx.Response(404))
    first = await client.get("https://arxiv.org/ok")
    second = await client.get("https://arxiv.org/ok")
    assert first.content == second.content == b"body" and second.from_cache
    await client.get("https://arxiv.org/missing")
    again = await client.get("https://arxiv.org/missing")
    assert again.status == 404 and again.from_cache
    assert ok.call_count == 1 and missing.call_count == 1


async def test_client_ttl_expires(db: Database, clock: FakeClock, mock: respx.MockRouter) -> None:
    now = [1_000_000.0]
    client = PoliteClient(db, clock=clock, wall=lambda: now[0])
    route = mock.get("https://arxiv.org/q").mock(return_value=httpx.Response(200, content=b"v"))
    await client.get("https://arxiv.org/q", ttl=60)
    now[0] += 30
    await client.get("https://arxiv.org/q", ttl=60)
    now[0] += 60
    await client.get("https://arxiv.org/q", ttl=60)
    assert route.call_count == 2
    await client.aclose()


async def test_client_retries_429_with_retry_after(
    client: PoliteClient, clock: FakeClock, mock: respx.MockRouter
) -> None:
    route = mock.get("https://arxiv.org/busy").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(503),
            httpx.Response(200, content=b"done"),
        ]
    )
    result = await client.get("https://arxiv.org/busy")
    assert result.content == b"done" and route.call_count == 3
    assert 7 in clock.slept


async def test_client_gives_up_on_network_errors(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get("https://arxiv.org/down").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(FetchError, match=r"Couldn't reach arxiv.org"):
        await client.get("https://arxiv.org/down")


# -- source extraction --------------------------------------------------------------------


def test_extract_source_tarball_is_safe() -> None:
    files = extract_source(latex_tarball({"fig.png": b"\x89PNG"}, evil=True))
    assert files is not None
    assert set(files) == {"main.tex", "sections/method.tex"}  # no ../evil.tex, symlink, or png


def test_extract_source_single_gzip_and_pdf() -> None:
    files = extract_source(gz_single_tex())
    assert files is not None and list(files) == ["main.tex"]
    assert extract_source(synthetic_pdf()) is None


# -- pipeline -----------------------------------------------------------------------------


def arxiv_meta(mock: respx.MockRouter, vid: str = "2106.09685v2") -> None:
    mock.get(API_URL).mock(
        return_value=httpx.Response(200, content=atom((vid, "Official Title", ["Ada Example"])))
    )


async def test_pipeline_prefers_arxiv_html(client: PoliteClient, mock: respx.MockRouter) -> None:
    arxiv_meta(mock)
    html = mock.get("https://arxiv.org/html/2106.09685v2").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "tiny_latexml.html").read_bytes())
    )
    eprint = mock.get(url__startswith="https://arxiv.org/e-print/")
    report = await fetch_and_parse(PaperRef(arxiv_id="2106.09685"), client)
    doc = report.doc
    assert doc.paper_id == "arxiv:2106.09685v2" and doc.ref.version == "v2"
    assert doc.ref.source_kind == "arxiv_html"
    assert doc.title == "Official Title" and doc.authors == ["Ada Example"]  # metadata wins
    assert html.called and not eprint.called
    assert report.attempts == ["arxiv_html: ok"]


async def test_pipeline_falls_back_to_ar5iv_then_latex(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    arxiv_meta(mock)
    mock.get("https://arxiv.org/html/2106.09685v2").mock(return_value=httpx.Response(404))
    mock.get("https://ar5iv.labs.arxiv.org/html/2106.09685").mock(
        return_value=httpx.Response(200, text="<html><body>No HTML yet</body></html>")
    )
    mock.get("https://arxiv.org/e-print/2106.09685v2").mock(
        return_value=httpx.Response(200, content=latex_tarball())
    )
    report = await fetch_and_parse(PaperRef(arxiv_id="2106.09685", version="v2"), client)
    assert report.doc.ref.source_kind == "arxiv_latex"
    assert [a.split(":")[0] for a in report.attempts] == ["arxiv_html", "ar5iv", "latex"]
    assert report.doc.by_kind("equation")


async def test_pipeline_falls_back_to_pdf(client: PoliteClient, mock: respx.MockRouter) -> None:
    arxiv_meta(mock)
    mock.get(url__startswith="https://arxiv.org/html/").mock(return_value=httpx.Response(404))
    mock.get(url__startswith="https://ar5iv.labs.arxiv.org/").mock(return_value=httpx.Response(404))
    mock.get(url__startswith="https://arxiv.org/e-print/").mock(
        return_value=httpx.Response(200, content=synthetic_pdf())  # PDF-only submission
    )
    pdf = mock.get(url__startswith="https://arxiv.org/pdf/")
    report = await fetch_and_parse(PaperRef(arxiv_id="2106.09685", version="v2"), client)
    assert report.doc.ref.source_kind == "pdf" and report.doc.low_structure
    assert not pdf.called  # the e-print already was the PDF


async def test_pipeline_all_sources_fail(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(API_URL).mock(return_value=httpx.Response(503))
    mock.get(url__startswith="https://arxiv.org/").mock(return_value=httpx.Response(404))
    mock.get(url__startswith="https://ar5iv.labs.arxiv.org/").mock(return_value=httpx.Response(404))
    with pytest.raises(IngestError, match="Try again, or upload the PDF"):
        await fetch_and_parse(PaperRef(arxiv_id="2106.09685", version="v2"), client)


async def test_pipeline_doi_pdf(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get("https://example.org/p.pdf").mock(
        return_value=httpx.Response(200, content=synthetic_pdf())
    )
    ref = PaperRef(doi="10.1000/J", title="Journal Paper", source_kind="pdf")
    report = await fetch_and_parse(ref, client, pdf_url="https://example.org/p.pdf")
    assert report.doc.paper_id == "doi:10.1000/j" and report.doc.title == "Journal Paper"
    with pytest.raises(IngestError, match="no open-access PDF"):
        await fetch_and_parse(ref, client)


async def test_ingest_ref_stores_and_reuses(
    client: PoliteClient, db: Database, mock: respx.MockRouter
) -> None:
    arxiv_meta(mock)
    html = mock.get("https://arxiv.org/html/2106.09685v2").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "tiny_latexml.html").read_bytes())
    )
    ref = PaperRef(arxiv_id="2106.09685", version="v2")
    first = await ingest_ref(ref, client, db)
    second = await ingest_ref(ref, client, db)
    assert not first.from_store and second.from_store
    assert second.doc == first.doc
    assert html.call_count == 1


def test_parse_upload() -> None:
    doc = parse_upload(synthetic_pdf(), "mine.pdf")
    assert doc.paper_id.startswith("upload:") and doc.ref.source_kind == "upload"
    with pytest.raises(IngestError, match="doesn't look like a PDF"):
        parse_upload(b"not a pdf", "notes.txt")


def test_fixture_json_roundtrip() -> None:
    doc = parse_upload(synthetic_pdf())
    assert json.loads(doc.model_dump_json())["low_structure"] is True
