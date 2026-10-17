from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from portolan.cli import app as cli
from portolan.ingest.arxiv import API_URL
from tests.ingest_helpers import FIXTURES
from tests.repo_helpers import mock_hf, mock_repo, mock_search, repo_json
from tests.test_ingest_cli import _no_politeness_delay  # pyright: ignore[reportUnusedImport]  # noqa: F401
from tests.test_ingest_fetch import atom
from tests.test_repo_upload import make_zip

runner = CliRunner()
ARXIV = "2106.09685"


def mock_paper(mock: respx.MockRouter) -> None:
    mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=atom(
                (f"{ARXIV}v2", "Toy Attention", ["Ada Example"]),
                # Real arXiv abstracts keep code links as plain text.
                summary="We describe toy attention. Code: https://github.com/example/toy-attention",
            ),
        )
    )
    mock.get(f"https://arxiv.org/html/{ARXIV}v2").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "tiny_latexml.html").read_bytes())
    )


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_repos_lists_candidates_and_picks_the_official_one(mock: respx.MockRouter) -> None:
    mock_paper(mock)
    mock_hf(mock, ARXIV)
    # The fixture's abstract links github.com/example/toy-attention.
    mock_repo(mock, "example/toy-attention", stars=12, owner_type="Organization",
              readme="Toy attention, arXiv 2106.09685")  # fmt: skip
    mock_repo(mock, "fan/toy", stars=3)
    mock_search(mock, repo_json("fan/toy", stars=3))
    project_page = mock.get("https://example.org/project").mock(return_value=httpx.Response(404))

    result = runner.invoke(cli, ["repos", ARXIV])
    assert project_page.called  # the paper's project-page link is checked for repo links
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Official code found: example/toy-attention" in out
    assert "1. example/toy-attention  [official] ★ 12" in out
    assert "2. fan/toy  [unofficial] ★ 3" in out
    assert "Using: example/toy-attention" in out

    # Stored results are reused (no new requests), and --use switches the choice.
    again = runner.invoke(cli, ["repos", ARXIV, "--use", "2"])
    assert again.exit_code == 0, again.output
    assert "Using: fan/toy" in again.output
    assert "network request" not in again.output


def test_repos_upload_zip(mock: respx.MockRouter, tmp_path: Path) -> None:
    mock_paper(mock)
    archive = tmp_path / "mine.zip"
    archive.write_bytes(make_zip({"mine/attn.py": b"def attend(): ...\n"}))
    result = runner.invoke(cli, ["repos", ARXIV, "--upload", str(archive)])
    assert result.exit_code == 0, result.output
    assert "Using your code for Toy Attention:" in result.output
    assert "upload-" in result.output


def test_repos_upload_bad_path(mock: respx.MockRouter, tmp_path: Path) -> None:
    mock_paper(mock)
    result = runner.invoke(cli, ["repos", ARXIV, "--upload", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "isn't a folder Portolan can read" in result.output
