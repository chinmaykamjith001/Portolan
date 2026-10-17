"""LaTeXML HTML (arxiv.org/html and ar5iv) → PaperDoc.

Structure verified against real arxiv.org/html and ar5iv pages (docs/DECISIONS.md): sections are
`section.ltx_section|ltx_subsection|ltx_subsubsection|ltx_paragraph|ltx_appendix` with an
`.ltx_title` heading whose `span.ltx_tag` holds the number; paragraphs are `div.ltx_para`;
equations are `table.ltx_equation` or `table.ltx_equationgroup` (one `tbody[id]` per numbered
row), with LaTeX in each `<math alttext>`; floats are `figure.ltx_table|ltx_figure` with a
`figcaption` whose `span.ltx_tag` holds "Table 1:"; cross-references are `a.ltx_ref[href="#S4.E3"]`.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from portolan.ingest.anchors import DocBuilder
from portolan.models.paper import Anchor, AnchorKind, PaperDoc, PaperRef


class NotLaTeXML(ValueError):
    """The page isn't a LaTeXML paper (e.g. an arXiv 'HTML not available' page)."""


SECTION_LEVELS = {
    "ltx_section": 1,
    "ltx_appendix": 1,
    "ltx_subsection": 2,
    "ltx_subsubsection": 3,
    "ltx_paragraph": 4,
    "ltx_subparagraph": 5,
}
EQUATION_CLASSES = {"ltx_equation", "ltx_equationgroup"}
SKIP_CLASSES = {"ltx_bibliography", "ltx_abstract", "ltx_authors", "ltx_dates", "ltx_pagination"}


def _classes(el: Tag) -> set[str]:
    raw = el.get("class")
    if raw is None:
        return set()
    return set(raw.split() if isinstance(raw, str) else raw)


def _attr(el: Tag, name: str) -> str:
    value = el.get(name)
    if value is None:
        return ""
    return value if isinstance(value, str) else " ".join(value)


def _is_equation(el: Tag) -> bool:
    return el.name == "table" and bool(_classes(el) & EQUATION_CLASSES)


def _float_kind(el: Tag) -> AnchorKind | None:
    if el.name != "figure":
        return None
    cls = _classes(el)
    if "ltx_table" in cls:
        return "table"
    if cls & {"ltx_float_algorithm", "ltx_algorithm"}:
        return "algorithm"
    if "ltx_figure" in cls or "ltx_float" in cls:
        return "figure"
    return None


def _math_latex(el: Tag) -> str:
    latex = _attr(el, "alttext")
    return re.sub(r"\\displaystyle\s*", "", latex).strip()


def text_of(el: Tag | NavigableString, *, skip_blocks: bool = True) -> str:
    """Readable text with inline math as $latex$ and footnotes in parentheses."""
    if isinstance(el, NavigableString):
        return str(el)
    if el.name == "math":
        return f"${_math_latex(el)}$"
    cls = _classes(el)
    if skip_blocks and (_is_equation(el) or _float_kind(el) is not None):
        return " "
    if "ltx_note_mark" in cls or "ltx_note_type" in cls or "ltx_tag_note" in cls:
        return ""
    if "ltx_note" in cls:
        content = el.find(class_="ltx_note_content")
        inner = text_of(content) if isinstance(content, Tag) else ""
        return f" (footnote: {inner.strip()})" if inner.strip() else ""
    if el.name in {"script", "style", "button"}:
        return ""
    parts = [text_of(child, skip_blocks=skip_blocks) for child in el.children]  # pyright: ignore[reportArgumentType]
    sep = " " if el.name in {"p", "li", "div", "td", "tr", "br"} else ""
    return sep.join(parts) if sep else "".join(parts)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _heading(section: Tag) -> tuple[str | None, str]:
    head = section.find(class_="ltx_title", recursive=False)
    if not isinstance(head, Tag):
        return None, ""
    tag = head.find(class_="ltx_tag")
    number = _clean(tag.get_text()) if isinstance(tag, Tag) else None
    if isinstance(tag, Tag):
        tag.extract()
    title = _clean(text_of(head))
    if isinstance(tag, Tag):
        head.insert(0, tag)  # keep the tree intact for any later reader
    return number or None, title


def _caption(fig: Tag) -> tuple[str | None, str]:
    cap = fig.find("figcaption")
    if not isinstance(cap, Tag):
        return None, ""
    tag = cap.find(class_="ltx_tag")
    number = _clean(tag.get_text()) if isinstance(tag, Tag) else None
    text = _clean(text_of(cap))
    if number and text.startswith(number):
        text = text[len(number) :].strip()
    return number, text


class _Walker:
    def __init__(self, b: DocBuilder) -> None:
        self.b = b

    def refs_and_links(self, el: Tag, from_id: str) -> None:
        for a in el.find_all("a"):
            href = _attr(a, "href")
            if href.startswith("#"):
                self.b.add_ref(from_id, href[1:])
            elif href.startswith(("http://", "https://")):
                self.b.add_link(href)

    def equation(self, table: Tag, parent: Anchor | None) -> None:
        if "ltx_equationgroup" in _classes(table):
            bodies = list(table.find_all("tbody"))
            if not bodies:
                bodies = [table]
        else:
            bodies = [table]
        pending: list[str] = []  # unnumbered rows join the next numbered row
        for body in bodies:
            latex = " ".join(_math_latex(m) for m in body.find_all("math"))
            tag = body.find(class_="ltx_tag_equation")
            number = _clean(tag.get_text()) if isinstance(tag, Tag) else None
            if not number:
                pending.append(latex)
                continue
            key = _attr(body, "id") or _attr(table, "id")
            anchor = self.b.equation(
                " \\\\ ".join([*pending, latex]), number, key=key, parent=parent
            )
            pending = []
            if body is not table:
                self.b.register_key(_attr(table, "id"), anchor.id)
        if pending:
            self.b.equation(" \\\\ ".join(pending), None, key=_attr(table, "id"), parent=parent)

    def float(self, fig: Tag, kind: AnchorKind) -> None:
        number, caption = _caption(fig)
        anchor = self.b.float(kind, caption, number, key=_attr(fig, "id") or None)
        self.refs_and_links(fig, anchor.id)

    def para(self, div: Tag) -> None:
        anchor = self.b.paragraph(text_of(div), key=_attr(div, "id") or None)
        if anchor is not None:
            self.refs_and_links(div, anchor.id)
        for el in div.find_all(["table", "figure"]):
            if el.find_parent("figure") is not None:
                continue
            if _is_equation(el):
                self.equation(el, anchor)
            else:
                kind = _float_kind(el)
                if kind is not None:
                    self.float(el, kind)

    def walk(self, el: Tag) -> None:
        for child in el.children:
            if not isinstance(child, Tag):
                continue
            cls = _classes(child)
            if cls & SKIP_CLASSES or child.name in {"header", "footer", "nav", "script", "style"}:
                continue
            level = next((SECTION_LEVELS[c] for c in cls if c in SECTION_LEVELS), None)
            if child.name == "section" and level is not None:
                if "ltx_appendix" in cls:
                    self.b.start_appendix()
                number, title = _heading(child)
                self.b.section(level, number, title, key=_attr(child, "id") or None)
                self.walk(child)
            elif "ltx_para" in cls:
                self.para(child)
            elif _is_equation(child):
                self.equation(child, None)
            elif (kind := _float_kind(child)) is not None:
                self.float(child, kind)
            elif "ltx_title" in cls:
                continue  # section headings are read by _heading
            else:
                self.walk(child)


def _authors(article: Tag) -> list[str]:
    names: list[str] = []
    for person in article.select(".ltx_authors .ltx_personname"):
        first_line = _clean(text_of(person).split("\n")[0])
        first_line = re.sub(r"\(footnote:.*?\)", "", first_line).strip(" ,")
        for name in re.split(r",|\band\b", first_line):
            name = re.sub(r"[*†‡§¶∗\d]+", "", name).strip()  # noqa: RUF001 (footnote marks)
            if name:
                names.append(name)
    return names


def parse_html(html: str | bytes, *, paper_id: str, ref: PaperRef) -> PaperDoc:
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", class_="ltx_document")
    if not isinstance(article, Tag):
        raise NotLaTeXML("Not a LaTeXML paper page")

    title_el = article.find(class_="ltx_title_document")
    title = ""
    if isinstance(title_el, Tag):
        title = re.sub(r"\(footnote:.*?\)", "", _clean(text_of(title_el))).strip()
    abstract_el = article.find(class_="ltx_abstract")
    abstract = ""
    if isinstance(abstract_el, Tag):
        abstract = _clean(" ".join(text_of(p) for p in abstract_el.find_all("p")))

    builder = DocBuilder()
    _Walker(builder).walk(article)
    if isinstance(abstract_el, Tag):
        for a in abstract_el.find_all("a"):
            builder.add_link(_attr(a, "href"))

    references = [_clean(text_of(li)) for li in article.select(".ltx_bibliography li.ltx_bibitem")]
    return builder.build(
        paper_id=paper_id,
        ref=ref.model_copy(update={"source_kind": "arxiv_html"}),
        title=title,
        authors=_authors(article),
        abstract=abstract,
        references=references,
    )
