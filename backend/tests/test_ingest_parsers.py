from __future__ import annotations

import pytest

from portolan.ingest.anchors import DocBuilder, clean_number, extract_symbols, slugify
from portolan.ingest.parse_html import NotLaTeXML, parse_html
from portolan.ingest.parse_latex import (
    collect_macros,
    expand_macros,
    flatten,
    parse_latex,
    split_rows,
    strip_comments,
)
from portolan.ingest.parse_pdf import parse_pdf
from portolan.models.paper import PaperDoc, PaperRef
from tests.ingest_helpers import FIXTURES, latex_files, synthetic_pdf

REF = PaperRef(arxiv_id="0000.00000", version="v1")


def ids(doc: PaperDoc, kind: str) -> list[str]:
    return [a.id for a in doc.anchors if a.kind == kind]


# -- builder ------------------------------------------------------------------------------


def test_builder_ids_labels_and_order() -> None:
    b = DocBuilder()
    b.paragraph("Front matter text.")
    b.section(1, "3", "Model")
    p1 = b.paragraph("First.")
    b.paragraph("Second.")
    eq = b.equation("a=b", "(4)")
    b.section(2, "3.2", "Attention")
    b.section(3, None, "Scaled dot-product attention!")
    b.equation("x", None)
    b.float("table", "Settings", "Table 2:")
    b.section(1, "3", "Duplicate number")
    doc = b.build(paper_id="p", ref=REF, title=" T ", authors=[" A ", ""], abstract="")

    assert [a.id for a in doc.anchors] == [
        "para:front:1", "sec:3", "para:3:1", "para:3:2", "eq:4", "sec:3.2",
        "sec:3.2.scaled-dot-product-attention", "eq:3.2.scaled-dot-product-attention:u1",
        "tab:2", "sec:3~2",
    ]  # fmt: skip
    assert [a.order for a in doc.anchors] == list(range(len(doc.anchors)))
    assert p1 is not None and p1.label == "§3 ¶1"
    assert eq.label == "Eq. 4" and eq.parent_id == "para:3:2"  # attaches to latest paragraph
    assert doc.anchor("sec:3.2.scaled-dot-product-attention").parent_id == "sec:3.2"  # type: ignore[union-attr]
    assert doc.anchor("tab:2").label == "Table 2"  # type: ignore[union-attr]
    assert doc.title == "T" and doc.authors == ["A"]


def test_builder_equation_refs_dedupe_and_ignore_non_equations() -> None:
    b = DocBuilder()
    b.section(1, "1", "Intro")
    eq = b.equation("e", "1", key="E1")
    p = b.paragraph("See (1) and (1) and §1.")
    assert p is not None
    b.add_ref(p.id, "E1")
    b.add_ref(p.id, "E1")
    b.register_key("S1", "sec:1")
    b.add_ref(p.id, "S1")
    b.add_ref(p.id, "missing")
    doc = b.build(paper_id="p", ref=REF, title="", authors=[], abstract="")
    assert doc.equation_refs == {eq.id: [p.id]}


def test_helpers() -> None:
    assert clean_number("Appendix B ") == "B"
    assert clean_number("(12)") == "12"
    assert clean_number("Table 3:") == "3"
    assert clean_number("  ") is None
    assert (
        slugify("Aren’t Existing Solutions Good Enough?") == "aren-t-existing-solutions-good-enough"  # noqa: RUF001
    )
    symbols = extract_symbols(
        [
            "where $d_k$ is the dimension of the keys and $h$ is the number of heads.",
            "$W_0$ is frozen.",
            "$\\alpha$ denotes the learning rate.",
        ]
    )
    assert symbols == {
        "d_k": "dimension of the keys",
        "h": "number of heads",
        "\\alpha": "learning rate",
    }


# -- HTML ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def html_doc() -> PaperDoc:
    return parse_html((FIXTURES / "tiny_latexml.html").read_bytes(), paper_id="p", ref=REF)


def test_html_metadata(html_doc: PaperDoc) -> None:
    assert html_doc.title == "Toy Attention: A Tiny Synthetic Paper"
    assert html_doc.authors == ["Ada Example", "Bo Sample"]
    assert html_doc.abstract.startswith("We describe toy attention.")
    assert html_doc.ref.source_kind == "arxiv_html"
    assert not html_doc.low_structure


def test_html_nested_sections_and_appendix(html_doc: PaperDoc) -> None:
    assert ids(html_doc, "section") == [
        "sec:1", "sec:1.notation", "sec:2", "sec:2.1", "sec:2.1.1", "sec:2.2", "sec:A", "sec:A.1",
    ]  # fmt: skip
    a = html_doc.anchor("sec:A")
    assert a is not None and a.is_appendix and a.label == "Appendix A"
    sub = html_doc.anchor("sec:2.1.1")
    assert sub is not None and sub.parent_id == "sec:2.1" and sub.level == 3
    assert not any(a.text.startswith("arXiv chrome") for a in html_doc.anchors)


def test_html_equations_numbered_grouped_and_unnumbered(html_doc: PaperDoc) -> None:
    assert ids(html_doc, "equation") == ["eq:1", "eq:2", "eq:3", "eq:2.1.1:u1"]
    eq1 = html_doc.anchor("eq:1")
    assert eq1 is not None and eq1.text == "S=\\frac{QK^{\\top}}{\\sqrt{d_{k}}}"
    assert eq1.parent_id == "para:2.1:1"
    assert html_doc.anchor("eq:3").text == "p_{ij} =e^{S_{ij}-m}"  # type: ignore[union-attr]
    assert html_doc.equation_refs == {
        "eq:1": ["para:1:2", "para:2.1.1:1"],
        "eq:2": ["para:2.1.1:1"],
    }


def test_html_paragraph_text_floats_links_refs(html_doc: PaperDoc) -> None:
    p = html_doc.anchor("para:1:1")
    assert p is not None
    assert "$d_{k}$ is the dimension of the keys." in p.text
    assert "(footnote: See the project page (https://example.org/project).)" in p.text
    assert html_doc.anchor("para:1:2").text == (  # type: ignore[union-attr]
        "The main result is Equation 1; details are in Appendix A."
    )
    assert html_doc.anchor("tab:1").text == "Hyperparameters."  # type: ignore[union-attr]
    assert html_doc.anchor("fig:1").text == "The toy model."  # type: ignore[union-attr]
    assert "https://github.com/example/toy-attention" in html_doc.links_found
    assert "https://example.org/project" in html_doc.links_found
    assert not any("cited-repo" in u for u in html_doc.links_found)  # bibliography excluded
    assert not any(u.split("//")[1] == "arxiv.org/" for u in html_doc.links_found)
    assert len(html_doc.references) == 1
    assert html_doc.symbols == {"d_{k}": "dimension of the keys", "h": "number of heads"}


def test_html_rejects_non_latexml() -> None:
    with pytest.raises(NotLaTeXML):
        parse_html("<html><body><p>HTML is not available for this paper.</p></body></html>",
                   paper_id="p", ref=REF)  # fmt: skip


# -- LaTeX --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tex_doc() -> PaperDoc:
    return parse_latex(latex_files(), paper_id="p", ref=REF)


def test_latex_preprocessing_helpers() -> None:
    assert strip_comments("a % note\nb 50\\% c") == "a \nb 50\\% c"
    files = {"main.tex": "A \\input{x} B", "x.tex": "X \\include{y}", "y.tex": "Y \\input{x}"}
    assert flatten("main.tex", files).split() == ["A", "X", "Y", "B"]  # cycle-safe
    src, macros = collect_macros(
        "\\newcommand{\\a}{alpha}\\newcommand\\b[2]{#2-#1}\\def\\c{gamma}"
        "\\newcommand{\\d}[1][x]{opt}\\renewcommand{\\section}{x} text \\a \\b{1}{2} \\c"
    )
    assert set(macros) == {"a", "b", "c"}
    assert expand_macros(src, macros).split() == ["text", "alpha", "2-1", "gamma"]
    assert split_rows("a \\\\ b \\begin{cases} x \\\\ y \\end{cases} \\\\[2pt] c") == [
        "a ",
        " b \\begin{cases} x \\\\ y \\end{cases} ",
        " c",
    ]


def test_latex_runaway_macros_are_bounded() -> None:
    src, macros = collect_macros("\\newcommand{\\x}{\\x\\x} \\x")
    assert len(expand_macros(src, macros)) < 10_000_000


def test_latex_metadata_and_macros(tex_doc: PaperDoc) -> None:
    assert tex_doc.title == "The ToyNet Paper"
    assert tex_doc.authors == ["Ada Example", "Bo Sample"]
    assert tex_doc.abstract == "We introduce ToyNet. Code: https://github.com/example/toynet."
    assert tex_doc.ref.source_kind == "arxiv_latex"


def test_latex_sections_input_comments_and_appendix(tex_doc: PaperDoc) -> None:
    assert ids(tex_doc, "section") == [
        "sec:1", "sec:2", "sec:2.1", "sec:2.1.1", "sec:acknowledgments", "sec:A", "sec:A.1",
    ]  # fmt: skip
    titles = [a.text for a in tex_doc.by_kind("section")]
    assert "Hidden section" not in titles and "Also hidden" not in titles and "Fake" not in titles
    assert tex_doc.anchor("sec:A").is_appendix  # type: ignore[union-attr]
    assert not tex_doc.anchor("sec:acknowledgments").is_appendix  # type: ignore[union-attr]
    intro = tex_doc.anchor("para:1:1")
    assert (
        intro is not None
        and intro.text == "ToyNet reads tokens in parallel. Escaped percent: 50% of tokens."
    )


def test_latex_equations_labels_and_refs(tex_doc: PaperDoc) -> None:
    assert ids(tex_doc, "equation") == ["eq:1", "eq:2", "eq:3", "eq:2.1:u1", "eq:2.1.1:u1"]
    assert tex_doc.anchor("eq:1").text == "S = \\frac{Q K^\\top}{\\sqrt{d_k}}"  # type: ignore[union-attr]
    assert tex_doc.anchor("eq:3").text == "t &= S_{ij} - m \\\\ p_{ij} &= e^{t}"  # type: ignore[union-attr]
    para = tex_doc.anchor("para:2.1:1")
    assert para is not None
    assert (
        para.text
        == "We compute scores and normalize them with a softmax, as in Eq. (1) and Eq. 2, Eq. 3."
    )
    assert tex_doc.anchor("para:A:1").text == "See Eq. (1)."  # type: ignore[union-attr]
    assert tex_doc.equation_refs == {
        "eq:1": ["para:2.1:1", "para:A:1"], "eq:2": ["para:2.1:1"], "eq:3": ["para:2.1:1"],
    }  # fmt: skip


def test_latex_floats_bibliography_symbols(tex_doc: PaperDoc) -> None:
    assert tex_doc.anchor("tab:1").text == "Hyperparameters of ToyNet."  # type: ignore[union-attr]
    assert tex_doc.anchor("fig:1").text == "The ToyNet architecture."  # type: ignore[union-attr]
    see = tex_doc.anchor("para:2.1.1:2")
    assert see is not None and see.text == "See Table 1 and Figure 1; the proof is in Appendix A."
    stab = tex_doc.anchor("para:2.1.1:1")
    assert (
        stab is not None and "\\operatorname{arg\\,max}" in stab.text and "\\epsilon" in stab.text
    )
    assert tex_doc.references == ["A. Person. A cited work.", "B. Person. Another work."]
    assert tex_doc.symbols == {"\\mathbf{x}": "input vector", "d": "model width"}
    assert tex_doc.links_found == ["https://github.com/example/toynet"]


def test_latex_numberwithin_section() -> None:
    files = {
        "main.tex": (
            b"\\documentclass{article}\\numberwithin{equation}{section}\\begin{document}"
            b"\\section{A}\n\\begin{equation}a\\end{equation}\n\\section{B}\n"
            b"\\begin{equation}b\\end{equation}\\begin{equation}c\\tag{*}\\end{equation}\\end{document}"
        )
    }
    doc = parse_latex(files, paper_id="p", ref=REF)
    assert ids(doc, "equation") == ["eq:1.1", "eq:2.1", "eq:*"]


# -- PDF ----------------------------------------------------------------------------------


def test_pdf_heuristic_structure() -> None:
    doc = parse_pdf(synthetic_pdf(), paper_id="p", ref=PaperRef(source_kind="pdf"))
    assert doc.low_structure
    assert doc.title == "A Synthetic PDF Paper"
    assert doc.abstract.startswith("We study a toy model")
    assert ids(doc, "section") == ["sec:1", "sec:2", "sec:2.1", "sec:A"]
    assert doc.anchor("sec:A").is_appendix  # type: ignore[union-attr]
    eq = doc.anchor("eq:1")
    assert eq is not None and eq.text == "S = Q K^T / sqrt(d)"
    assert ids(doc, "table") == ["tab:1"]
    assert doc.references == ["[1] A. Person. A cited work. 2020."]
    assert "https://github.com/example/pdfpaper." not in doc.links_found
    assert "https://github.com/example/pdfpaper" in doc.links_found
