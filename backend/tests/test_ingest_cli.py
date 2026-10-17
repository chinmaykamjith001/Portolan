from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from portolan.cli import app as cli
from portolan.ingest.arxiv import API_URL
from portolan.ingest.http import PoliteClient
from tests.ingest_helpers import FIXTURES, synthetic_pdf
from tests.test_ingest_fetch import atom

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_politeness_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PoliteClient.__init__

    def fast_init(self: PoliteClient, *args: object, **kwargs: object) -> None:
        kwargs["spacing"] = {"export.arxiv.org": 0, "arxiv.org": 0, "ar5iv.labs.arxiv.org": 0}
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PoliteClient, "__init__", fast_init)


@respx.mock(assert_all_called=False)
def test_ingest_dry_run_prints_tree_and_counts(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(API_URL).mock(
        return_value=httpx.Response(200, content=atom(("2106.09685v2", "Toy Attention", ["Ada"])))
    )
    respx_mock.get("https://arxiv.org/html/2106.09685v2").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "tiny_latexml.html").read_bytes())
    )
    result = runner.invoke(cli, ["ingest", "https://arxiv.org/abs/2106.09685", "--dry-run"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "arxiv:2106.09685v2 · source: arxiv_html" in out
    assert "§2.1.1  Stability" in out
    assert "§2.1  Scores  (1 ¶, 1 eq)" in out
    assert "Appendix A  Proofs" in out
    assert (
        "Anchors: 8 sections, 8 paragraphs, 4 equations, 1 tables, 1 figures, 0 algorithms" in out
    )
    assert "Code links in the paper: https://github.com/example/toy-attention" in out
    assert "2 network request(s)" in out

    # Dry run stores nothing, but external responses are cached: a second run makes no requests.
    again = runner.invoke(cli, ["ingest", "https://arxiv.org/abs/2106.09685", "--dry-run"])
    assert again.exit_code == 0, again.output
    assert "network request" not in again.output
    assert "source: arxiv_html · arxiv_html: ok" in again.output


@respx.mock(assert_all_called=False)
def test_ingest_title_asks_which_match(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200, content=atom(("1706.03762v7", "Attention Is All You Need", ["A", "B", "C", "D"]))
        )
    )
    result = runner.invoke(cli, ["ingest", "attention is all you need", "--dry-run"], input="0\n")
    assert result.exit_code == 1
    assert "1. Attention Is All You Need — A, B, C et al. (2021) arXiv:1706.03762" in result.output
    assert "Cancelled." in result.output


def test_ingest_local_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(synthetic_pdf())
    result = runner.invoke(cli, ["ingest", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "Parsed from PDF; structure may be approximate." in result.output
    assert "source: upload" in result.output


def test_ingest_reports_plain_errors() -> None:
    result = runner.invoke(cli, ["ingest", "https://example.com/x", "--dry-run"])
    assert result.exit_code == 1
    assert "isn't an arXiv, DOI or paper-page link" in result.output
    assert "Traceback" not in result.output
