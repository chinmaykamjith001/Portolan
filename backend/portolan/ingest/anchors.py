"""Shared document builder: every parser (HTML, LaTeX, PDF) emits structure through this, so all
three produce the same stable anchor ids, labels and ordering.

Id scheme:
  sections    sec:3, sec:3.2.1, sec:A, sec:A.1; unnumbered → sec:<parent>.<slug> or sec:<slug>
  paragraphs  para:<section>:<n>      (n counts paragraphs directly under that section, from 1)
  equations   eq:<number>; unnumbered → eq:<section>:u<n>
  floats      tab:<n>, fig:<n>, alg:<n>; unnumbered → tab:<section>:u<n>
"""

from __future__ import annotations

import re
from collections import defaultdict

from portolan.models.paper import Anchor, AnchorKind, PaperDoc, PaperRef

FRONT = "front"  # pseudo-section for content before the first heading

_FLOAT_PREFIX: dict[AnchorKind, tuple[str, str]] = {
    "table": ("tab", "Table"),
    "figure": ("fig", "Figure"),
    "algorithm": ("alg", "Algorithm"),
}

# Stops before ", and $y$ ..." so chained definitions each get their own entry.
_MEANING = r"((?:(?!\s+and\s+\$)[^.;,:$()]){3,80})"
# "where $d_k$ is the dimension of the keys" — an article is required after is/are so that
# statements like "$W_0$ is frozen" are not mistaken for definitions.
_SYMBOL_DEF = re.compile(
    r"(?:\bwhere|\band|,|;)\s+\$([^$]{1,40})\$\s+(?:is|are)\s+(?:the|a|an)\s+" + _MEANING,
    re.IGNORECASE,
)
_SYMBOL_DENOTES = re.compile(
    r"\$([^$]{1,40})\$\s+(?:denotes|denote|represents|stands for)\s+(?:the\s+|a\s+|an\s+)?"
    + _MEANING,
    re.IGNORECASE,
)
_MEANING_STOP = re.compile(r"\s+(?:and|which|that|with|for|when|where|in|of size)\b.*$", re.I)


def tidy(text: str) -> str:
    """Collapse whitespace and drop spaces left before punctuation by markup joins."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ([,.;:!?)])(?=\s|$)", r"\1", text)


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "untitled"


def clean_number(raw: str | None) -> str | None:
    """'3.2 ' → '3.2'; '(4)' → '4'; 'Appendix B' → 'B'; 'Table 3:' → '3'."""
    if raw is None:
        return None
    s = raw.strip()
    s = re.sub(r"^(appendix|table|figure|fig\.|algorithm|section|§)\s*", "", s, flags=re.I)
    s = s.strip().strip("():.").strip()
    return s or None


def _clean_symbol(sym: str) -> str:
    return re.sub(r"\\displaystyle\s*", "", sym).strip()


def extract_symbols(texts: list[str]) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for text in texts:
        for pattern in (_SYMBOL_DEF, _SYMBOL_DENOTES):
            for m in pattern.finditer(text):
                sym = _clean_symbol(m.group(1))
                meaning = re.sub(r"\s+", " ", m.group(2)).strip()
                meaning = _MEANING_STOP.sub("", meaning).strip()
                meaning = re.sub(r"\s+(?:of|to|by|on|at|from|between)$", "", meaning)
                if (
                    re.search(r"[A-Za-z\\]", sym)
                    and len(meaning) >= 3
                    and not meaning.endswith("-")
                    and not re.match(r"(?:most|more|less|very|also|not)\b", meaning, re.I)
                    and sym not in symbols
                ):
                    symbols[sym] = meaning
    return symbols


class DocBuilder:
    def __init__(self) -> None:
        self.anchors: list[Anchor] = []
        self._ids: set[str] = set()
        self._stack: list[Anchor] = []  # open sections, outermost first
        self._para_count: dict[str, int] = defaultdict(int)
        self._unnumbered: dict[tuple[str, str], int] = defaultdict(int)
        self._last_para: dict[str, Anchor] = {}
        self._keys: dict[str, str] = {}  # source key (html id / \label) → anchor id
        self._refs: list[tuple[str, str]] = []  # (from anchor id, target key)
        self.links: list[str] = []
        self.in_appendix = False

    # -- helpers ----------------------------------------------------------------------------

    def _unique(self, anchor_id: str) -> str:
        if anchor_id not in self._ids:
            self._ids.add(anchor_id)
            return anchor_id
        n = 2
        while f"{anchor_id}~{n}" in self._ids:
            n += 1
        self._ids.add(f"{anchor_id}~{n}")
        return f"{anchor_id}~{n}"

    def _add(self, **kw: object) -> Anchor:
        anchor = Anchor.model_validate({**kw, "order": len(self.anchors)})
        self.anchors.append(anchor)
        return anchor

    @property
    def current_section(self) -> Anchor | None:
        return self._stack[-1] if self._stack else None

    def _scope(self) -> str:
        sec = self.current_section
        return sec.id.removeprefix("sec:") if sec else FRONT

    def _scope_label(self) -> str:
        sec = self.current_section
        return sec.label if sec else "front matter"

    def register_key(self, key: str | None, anchor_id: str) -> None:
        if key:
            self._keys.setdefault(key, anchor_id)

    def add_ref(self, from_id: str, key: str) -> None:
        self._refs.append((from_id, key))

    def add_link(self, url: str) -> None:
        url = url.strip().rstrip(".,;)")
        if url.startswith(("http://", "https://")) and url not in self.links:
            self.links.append(url)

    # -- structure --------------------------------------------------------------------------

    def start_appendix(self) -> None:
        self.in_appendix = True

    def section(
        self, level: int, number: str | None, title: str, *, key: str | None = None
    ) -> Anchor:
        while self._stack and (self._stack[-1].level or 0) >= level:
            self._stack.pop()
        parent = self.current_section
        number = clean_number(number)
        # Run-in headings ("Encoder:", "Notation.") carry punctuation that isn't part of the title.
        title = re.sub(r"\s+", " ", title).strip().rstrip(":.").strip() or "Untitled"
        if number:
            anchor_id = f"sec:{number}"
            label = f"Appendix {number}" if self.in_appendix and "." not in number else f"§{number}"
        else:
            prefix = f"{parent.id.removeprefix('sec:')}." if parent else ""
            anchor_id = f"sec:{prefix}{slugify(title)}"
            label = title
        anchor = self._add(
            id=self._unique(anchor_id),
            kind="section",
            label=label,
            text=title,
            parent_id=parent.id if parent else None,
            level=level,
            is_appendix=self.in_appendix,
        )
        self._stack.append(anchor)
        self.register_key(key, anchor.id)
        return anchor

    def paragraph(self, text: str, *, key: str | None = None) -> Anchor | None:
        text = tidy(text)
        if not text:
            return None
        scope = self._scope()
        self._para_count[scope] += 1
        n = self._para_count[scope]
        sec = self.current_section
        anchor = self._add(
            id=self._unique(f"para:{scope}:{n}"),
            kind="paragraph",
            label=f"{self._scope_label()} ¶{n}",
            text=text,
            parent_id=sec.id if sec else None,
            is_appendix=self.in_appendix,
        )
        self._last_para[scope] = anchor
        self.register_key(key, anchor.id)
        return anchor

    def set_text(self, anchor: Anchor, text: str) -> None:
        anchor.text = tidy(text)

    def equation(
        self,
        latex: str,
        number: str | None,
        *,
        key: str | None = None,
        parent: Anchor | None = None,
    ) -> Anchor:
        number = clean_number(number)
        scope = self._scope()
        if number:
            anchor_id, label = f"eq:{number}", f"Eq. {number}"
        else:
            self._unnumbered[(scope, "eq")] += 1
            anchor_id = f"eq:{scope}:u{self._unnumbered[(scope, 'eq')]}"
            label = f"Unnumbered equation in {self._scope_label()}"
        if parent is None:
            parent = self._last_para.get(scope) or self.current_section
        anchor = self._add(
            id=self._unique(anchor_id),
            kind="equation",
            label=label,
            text=latex.strip(),
            parent_id=parent.id if parent else None,
            is_appendix=self.in_appendix,
        )
        self.register_key(key, anchor.id)
        return anchor

    def float(
        self, kind: AnchorKind, caption: str, number: str | None, *, key: str | None = None
    ) -> Anchor:
        prefix, word = _FLOAT_PREFIX[kind]
        number = clean_number(number)
        scope = self._scope()
        if number:
            anchor_id, label = f"{prefix}:{number}", f"{word} {number}"
        else:
            self._unnumbered[(scope, prefix)] += 1
            anchor_id = f"{prefix}:{scope}:u{self._unnumbered[(scope, prefix)]}"
            label = f"{word} in {self._scope_label()}"
        sec = self.current_section
        anchor = self._add(
            id=self._unique(anchor_id),
            kind=kind,
            label=label,
            text=re.sub(r"\s+", " ", caption).strip(),
            parent_id=sec.id if sec else None,
            is_appendix=self.in_appendix,
        )
        self.register_key(key, anchor.id)
        return anchor

    # -- output -----------------------------------------------------------------------------

    def resolve(self, key: str) -> str | None:
        return self._keys.get(key)

    def build(
        self,
        *,
        paper_id: str,
        ref: PaperRef,
        title: str,
        authors: list[str],
        abstract: str,
        references: list[str] | None = None,
        low_structure: bool = False,
    ) -> PaperDoc:
        kinds = {a.id: a.kind for a in self.anchors}
        equation_refs: dict[str, list[str]] = {}
        for from_id, key in self._refs:
            target = self._keys.get(key)
            if target and kinds.get(target) == "equation" and from_id != target:
                refs = equation_refs.setdefault(target, [])
                if from_id not in refs:
                    refs.append(from_id)
        paragraphs = [a.text for a in self.anchors if a.kind == "paragraph"]
        return PaperDoc(
            paper_id=paper_id,
            ref=ref,
            title=re.sub(r"\s+", " ", title).strip(),
            authors=[a for a in (re.sub(r"\s+", " ", x).strip() for x in authors) if a],
            abstract=re.sub(r"\s+", " ", abstract).strip(),
            anchors=self.anchors,
            equation_refs=equation_refs,
            symbols=extract_symbols([abstract, *paragraphs]),
            links_found=self.links,
            references=references or [],
            low_structure=low_structure,
        )
