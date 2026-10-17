"""arXiv LaTeX source → PaperDoc (the fallback when no LaTeXML HTML exists).

Pipeline: pick the main file → strip comments → flatten \\input/\\include → drop \\iffalse and
comment blocks → expand simple macros (\\newcommand, \\renewcommand, \\providecommand,
parameterless \\def, \\DeclareMathOperator) → walk the body into events (sections, paragraphs,
display equations, floats, bibliography) → number them like LaTeX would → emit anchors.

Known limits (docs/DECISIONS.md): custom counters and most packages that change numbering are not
modelled; `\\numberwithin{equation}{section}` is.
"""

from __future__ import annotations

import importlib
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from portolan.ingest.anchors import DocBuilder
from portolan.models.paper import AnchorKind, PaperDoc, PaperRef

MAX_INCLUDE_DEPTH = 20
MAX_EXPANSION_PASSES = 8
MAIN_NAMES = ("main.tex", "ms.tex", "paper.tex", "arxiv.tex", "article.tex")

SECTION_LEVELS = {
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
    "subparagraph": 5,
}
SINGLE_MATH = {"equation", "multline", "displaymath", "math"}
MULTI_MATH = {"align", "gather", "eqnarray", "flalign", "alignat", "multline"}
FLOATS: dict[str, AnchorKind] = {
    "table": "table",
    "wraptable": "table",
    "sidewaystable": "table",
    "figure": "figure",
    "wrapfigure": "figure",
    "sidewaysfigure": "figure",
    "algorithm": "algorithm",
    "algorithm2e": "algorithm",
}
PROTECTED = {
    "section", "subsection", "subsubsection", "paragraph", "subparagraph", "begin", "end", "label",
    "ref", "eqref", "cref", "Cref", "autoref", "cite", "citep", "citet", "caption", "input",
    "include", "appendix", "item", "title", "author", "footnote", "url", "href", "bibitem",
}  # fmt: skip

_COMMENT = re.compile(r"(?<!\\)%[^\n]*")
_INPUT = re.compile(r"\\(?:input|include|subfile)\s*(?:\{([^}]*)\}|([^\s{}\\]+))")
_IFFALSE = re.compile(r"\\iffalse\b.*?\\fi\b", re.S)
_COMMENT_ENV = re.compile(r"\\begin\{comment\}.*?\\end\{comment\}", re.S)
_LABEL = re.compile(r"\\label\s*\{([^}]*)\}")
_REF = re.compile(
    r"\\(?P<cmd>ref|eqref|autoref|cref|Cref|pageref|vref|Vref|subref|cpageref)\*?\s*\{(?P<keys>[^}]*)\}"
)
_CITE = re.compile(r"\\(?:cite[a-zA-Z]*|nocite)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")
_URL_TEXT = re.compile(r"https?://[^\s{}\\]+")
_NONUMBER = re.compile(r"\\(?:nonumber|notag)\b")
_TAG = re.compile(r"\\tag\*?\s*\{([^}]*)\}")

_TOKEN = re.compile(
    r"\\(?P<sec>section|subsection|subsubsection|paragraph|subparagraph)(?P<star>\*?)\s*(?:\[[^\]]*\])?\s*(?=\{)"
    r"|\\(?P<appendix>appendix)(?![A-Za-z])"
    r"|\\begin\s*\{(?P<env>[A-Za-z0-9]+)(?P<envstar>\*?)\}"
    r"|(?P<dbracket>\\\[)"
    r"|(?P<ddollar>\$\$)"
    r"|(?P<blank>\n[ \t]*\n)"
)


# -- small LaTeX helpers ------------------------------------------------------------------


def read_group(s: str, i: int, open_: str = "{", close: str = "}") -> tuple[str, int] | None:
    """If s[i:] (after whitespace) starts a balanced group, return (content, index after it)."""
    while i < len(s) and s[i] in " \t\n":
        i += 1
    if i >= len(s) or s[i] != open_:
        return None
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{" or (open_ != "{" and c == open_):
            depth += 1
        elif c == "}" or (close != "}" and c == close):
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return None


def replace_command(s: str, name: str, fn: Callable[[list[str]], str], nargs: int = 1) -> str:
    """Replace `\\name{a}{b}...` (balanced) with fn([a, b, ...])."""
    pattern = re.compile(rf"\\{name}\*?(?![A-Za-z])")
    out: list[str] = []
    pos = 0
    for m in pattern.finditer(s):
        if m.start() < pos:
            continue
        j = m.end()
        opt = read_group(s, j, "[", "]")
        if opt:
            j = opt[1]
        args: list[str] = []
        for _ in range(nargs):
            g = read_group(s, j)
            if g is None:
                break
            args.append(g[0])
            j = g[1]
        if len(args) < nargs:
            continue
        out.append(s[pos : m.start()])
        out.append(fn(args))
        pos = j
    out.append(s[pos:])
    return "".join(out)


def find_env_end(s: str, start: int, env: str) -> tuple[str, int]:
    """Content of an environment whose \\begin ends at `start`, and the index after its \\end."""
    pattern = re.compile(rf"\\(begin|end)\s*\{{{re.escape(env)}\}}")
    depth = 1
    for m in pattern.finditer(s, start):
        depth += 1 if m.group(1) == "begin" else -1
        if depth == 0:
            return s[start : m.start()], m.end()
    return s[start:], len(s)


def split_rows(body: str) -> list[str]:
    """Split an align-like body on top-level `\\\\` (not inside braces or nested environments)."""
    rows: list[str] = []
    depth = env_depth = 0
    start = i = 0
    while i < len(body):
        if body.startswith("\\begin", i):
            env_depth += 1
            i += 6
            continue
        if body.startswith("\\end", i):
            env_depth -= 1
            i += 4
            continue
        c = body[i]
        if c == "\\" and body.startswith("\\\\", i) and depth == 0 and env_depth == 0:
            rows.append(body[start:i])
            i += 2
            opt = re.match(r"\s*\[[^\]]*\]", body[i:])
            if opt:
                i += opt.end()
            start = i
            continue
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    rows.append(body[start:])
    return rows


# -- preprocessing ------------------------------------------------------------------------


def strip_comments(src: str) -> str:
    return _COMMENT.sub("", src)


def find_main(files: dict[str, str]) -> str | None:
    candidates = [
        name
        for name, text in files.items()
        if re.search(r"\\documentclass", text) and re.search(r"\\begin\s*\{document\}", text)
    ]
    if not candidates:
        return None
    for preferred in MAIN_NAMES:
        for name in candidates:
            if posixpath.basename(name).lower() == preferred:
                return name
    return max(candidates, key=lambda n: len(files[n]))


def flatten(main: str, files: dict[str, str]) -> str:
    base = posixpath.dirname(main)

    def lookup(name: str) -> str | None:
        name = name.strip().strip('"')
        for candidate in (name, f"{name}.tex"):
            for path in (candidate, posixpath.normpath(posixpath.join(base, candidate))):
                if path in files:
                    return path
        return None

    def expand(path: str, depth: int, seen: frozenset[str]) -> str:
        text = strip_comments(files[path])
        if depth >= MAX_INCLUDE_DEPTH:
            return text

        def sub(m: re.Match[str]) -> str:
            target = lookup(m.group(1) or m.group(2) or "")
            if target is None or target in seen:
                return ""
            return "\n" + expand(target, depth + 1, seen | {target}) + "\n"

        return _INPUT.sub(sub, text)

    return expand(main, 0, frozenset({main}))


@dataclass
class Macro:
    nargs: int
    body: str


def collect_macros(src: str) -> tuple[str, dict[str, Macro]]:
    """Remove simple macro definitions from `src` and return them."""
    macros: dict[str, Macro] = {}
    out: list[str] = []
    pos = 0
    pattern = re.compile(
        r"\\(?P<kind>newcommand|renewcommand|providecommand|DeclareMathOperator|def)(?P<star>\*?)"
    )
    for m in pattern.finditer(src):
        if m.start() < pos:
            continue
        j = m.end()
        name_m = re.match(r"\s*(?:\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+))", src[j:])
        if not name_m:
            continue
        name = name_m.group(1) or name_m.group(2)
        j += name_m.end()
        kind = m.group("kind")
        nargs = 0
        has_default = False
        if kind == "def":
            if not re.match(r"\s*\{", src[j:]):
                continue  # \def with parameter text (#1...): too irregular to expand safely
        elif kind != "DeclareMathOperator":
            n = re.match(r"\s*\[(\d)\]", src[j:])
            if n:
                nargs = int(n.group(1))
                j += n.end()
                default = read_group(src, j, "[", "]")
                if default:
                    has_default = True
                    j = default[1]
        body = read_group(src, j)
        if body is None:
            continue
        out.append(src[pos : m.start()])
        pos = body[1]
        if name in PROTECTED or has_default:
            continue
        text = body[0]
        if kind == "DeclareMathOperator":
            text = rf"\operatorname{{{text}}}"
        if kind == "providecommand" and name in macros:
            continue
        macros[name] = Macro(nargs, text)
    out.append(src[pos:])
    return "".join(out), macros


def expand_macros(src: str, macros: dict[str, Macro]) -> str:
    if not macros:
        return src
    pattern = re.compile(r"\\([A-Za-z@]+)(?![A-Za-z@])")
    limit = len(src) * 4 + 100_000
    for _ in range(MAX_EXPANSION_PASSES):
        out: list[str] = []
        pos = 0
        changed = False
        for m in pattern.finditer(src):
            if m.start() < pos:
                continue
            macro = macros.get(m.group(1))
            if macro is None:
                continue
            j = m.end()
            args: list[str] = []
            for _ in range(macro.nargs):
                g = read_group(src, j)
                if g is not None:
                    args.append(g[0])
                    j = g[1]
                else:
                    tok = re.match(r"\s*(\\[A-Za-z@]+|\S)", src[j:])
                    if tok is None:
                        break
                    args.append(tok.group(1))
                    j += tok.end()
            if len(args) < macro.nargs:
                continue
            body = macro.body
            for k, arg in enumerate(args, start=1):
                body = body.replace(f"#{k}", arg)
            out.append(src[pos : m.start()])
            # Keep a separator so "\foo bar" doesn't glue into the expansion.
            out.append(
                body + (" " if macro.nargs == 0 and re.match(r"[A-Za-z]", src[j : j + 1]) else "")
            )
            pos = j
            changed = True
        out.append(src[pos:])
        src = "".join(out)
        if not changed or len(src) > limit:
            break
    return src


# -- events -------------------------------------------------------------------------------


@dataclass
class SectionEv:
    level: int
    starred: bool
    title: str
    labels: list[str] = field(default_factory=list[str])
    number: str | None = None


@dataclass
class AppendixEv:
    pass


@dataclass
class EqEv:
    latex: str
    numbered: bool
    tag: str | None = None
    labels: list[str] = field(default_factory=list[str])
    number: str | None = None


@dataclass
class ParaEv:
    parts: list[str | EqEv] = field(default_factory=list[str | EqEv])

    def has_content(self) -> bool:
        return any(isinstance(p, EqEv) or p.strip() for p in self.parts)


@dataclass
class FloatEv:
    kind: AnchorKind
    caption: str
    labels: list[str] = field(default_factory=list[str])
    number: str | None = None


Event = SectionEv | AppendixEv | ParaEv | FloatEv


def _math_events(env: str, starred: bool, body: str) -> list[EqEv]:
    body = re.sub(r"^\s*\{\d+\}", "", body) if env == "alignat" else body
    multi = env in MULTI_MATH and env != "multline"
    rows = split_rows(body) if multi else [body]
    events: list[EqEv] = []
    pending: list[str] = []
    pending_labels: list[str] = []
    for row in rows:
        labels = _LABEL.findall(row)
        tag = _TAG.search(row)
        numbered = not starred and env not in {"displaymath", "math"} and not _NONUMBER.search(row)
        latex = _TAG.sub("", _NONUMBER.sub("", _LABEL.sub("", row))).strip()
        if not latex and not labels:
            continue
        if numbered or tag:
            events.append(
                EqEv(
                    latex=" \\\\ ".join([*pending, latex]),
                    numbered=True,
                    tag=tag.group(1) if tag else None,
                    labels=[*pending_labels, *labels],
                )
            )
            pending, pending_labels = [], []
        else:
            pending.append(latex)
            pending_labels.extend(labels)
    if pending:
        if events:
            events[-1].latex += " \\\\ " + " \\\\ ".join(pending)
            events[-1].labels.extend(pending_labels)
        else:
            events.append(EqEv(" \\\\ ".join(pending), numbered=False, labels=pending_labels))
    return events


def _caption(body: str) -> str:
    found: list[str] = []
    replace_command(body, "caption", lambda a: found.append(a[0]) or "")
    return found[0] if found else ""


def walk(body: str) -> tuple[list[Event], list[str]]:
    """Split the document body into structural events (and collect bibliography items)."""
    events: list[Event] = []
    bib: list[str] = []
    para = ParaEv()

    def close_para() -> None:
        nonlocal para
        if para.has_content():
            events.append(para)
        para = ParaEv()

    pos = 0
    while True:
        m = _TOKEN.search(body, pos)
        if m is None:
            para.parts.append(body[pos:])
            break
        para.parts.append(body[pos : m.start()])
        pos = m.end()
        if m.group("blank"):
            close_para()
        elif m.group("sec"):
            close_para()
            g = read_group(body, pos)
            title, pos = g if g else ("", pos)
            labels: list[str] = []
            lm = re.match(r"\s*\\label\s*\{([^}]*)\}", body[pos:])
            if lm:
                labels.append(lm.group(1))
                pos += lm.end()
            events.append(
                SectionEv(SECTION_LEVELS[m.group("sec")], bool(m.group("star")), title, labels)
            )
        elif m.group("appendix"):
            close_para()
            events.append(AppendixEv())
        elif m.group("dbracket"):
            end = body.find("\\]", pos)
            end = len(body) if end < 0 else end
            para.parts.extend(_math_events("displaymath", True, body[pos:end]))
            pos = min(len(body), end + 2)
        elif m.group("ddollar"):
            end = body.find("$$", pos)
            end = len(body) if end < 0 else end
            para.parts.extend(_math_events("displaymath", True, body[pos:end]))
            pos = min(len(body), end + 2)
        else:
            env, starred = m.group("env"), bool(m.group("envstar"))
            full = env + ("*" if starred else "")
            if env in SINGLE_MATH or env in MULTI_MATH:
                content, pos = find_env_end(body, pos, full)
                para.parts.extend(_math_events(env, starred, content))
            elif env in FLOATS:
                close_para()
                content, pos = find_env_end(body, pos, full)
                caption = _caption(content)
                events.append(FloatEv(FLOATS[env], caption, _LABEL.findall(content)))
            elif env == "thebibliography":
                close_para()
                content, pos = find_env_end(body, pos, full)
                bib.extend(_bibitems(content))
            elif env in {"abstract", "comment"}:
                _, pos = find_env_end(body, pos, full)
            elif env == "appendices":
                close_para()
                events.append(AppendixEv())
            else:
                para.parts.append(m.group(0))  # ordinary environment: keep as text
    close_para()
    return events, bib


def _bibitems(content: str) -> list[str]:
    items = re.split(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", content)[1:]
    return [t for t in (render(i, {}) for i in items) if t]


# -- numbering + rendering ----------------------------------------------------------------


def _letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


Labels = dict[str, tuple[str, str]]  # \label key → (name used by \cref, number as \ref prints it)


def number_events(events: list[Event], *, eq_within_section: bool) -> Labels:
    """Assign LaTeX-style numbers in place; return the label table."""
    labels: Labels = {}
    counters = [0, 0, 0]
    appendix = False
    eq = 0
    floats: dict[AnchorKind, int] = {"table": 0, "figure": 0, "algorithm": 0}
    words = {"table": "Table", "figure": "Figure", "algorithm": "Algorithm"}

    def section_number() -> str:
        top = _letter(counters[0]) if appendix else str(counters[0])
        return top

    for ev in events:
        if isinstance(ev, AppendixEv):
            appendix = True
            counters = [0, 0, 0]
        elif isinstance(ev, SectionEv):
            if not ev.starred and ev.level <= 3:
                counters[ev.level - 1] += 1
                for k in range(ev.level, 3):
                    counters[k] = 0
                parts = [section_number(), *(str(c) for c in counters[1 : ev.level])]
                ev.number = ".".join(parts)
                if ev.level == 1 and eq_within_section:
                    eq = 0
            name = "Appendix" if appendix and ev.level == 1 else "Section"
            for key in ev.labels:
                labels[key] = (name, ev.number or ev.title)
        elif isinstance(ev, FloatEv):
            if ev.caption:
                floats[ev.kind] += 1
                ev.number = str(floats[ev.kind])
            for key in ev.labels:
                labels[key] = (words[ev.kind], ev.number or "")
        else:
            for part in ev.parts:
                if not isinstance(part, EqEv):
                    continue
                if part.tag:
                    part.number = part.tag
                elif part.numbered:
                    eq += 1
                    part.number = f"{section_number()}.{eq}" if eq_within_section else str(eq)
                for key in part.labels:
                    labels[key] = ("Eq.", part.number or "")
    return labels


# pylatexenc ships no type stubs; keep its untyped surface here.
_latex2text: Any = importlib.import_module("pylatexenc.latex2text")
_TEXT: Any = _latex2text.LatexNodes2Text(math_mode="verbatim", keep_comments=False)


def _ref_text(cmd: str, key: str, labels: Labels) -> str:
    """What LaTeX prints: \\ref → "3", \\eqref → "(3)", \\cref → "Eq. 3"."""
    if cmd in {"pageref", "cpageref"}:
        return ""
    name, number = labels.get(key.strip(), ("", "??"))
    if cmd == "eqref":
        return f"({number})"
    if cmd in {"cref", "Cref", "autoref", "vref", "Vref"} and name:
        return f"{name} {number}"
    return number


def render(raw: str, labels: Labels) -> str:
    s = _REF.sub(
        lambda m: ", ".join(_ref_text(m["cmd"], k, labels) for k in m["keys"].split(",")), raw
    )
    s = _CITE.sub(lambda m: "[" + ", ".join(k.strip() for k in m.group(1).split(",")) + "]", s)
    s = _LABEL.sub("", s)
    s = replace_command(s, "footnote", lambda a: f" (footnote: {a[0]})")
    s = replace_command(s, "thanks", lambda _a: "")
    s = replace_command(s, "href", lambda a: f"{a[1]} ({a[0]})", nargs=2)
    s = replace_command(s, "url", lambda a: a[0])
    s = re.sub(r"\\\((.*?)\\\)", r"$\1$", s, flags=re.S)
    try:
        text = str(_TEXT.latex_to_text(s))
    except Exception:  # pylatexenc can choke on malformed input; fall back to a crude strip
        text = re.sub(r"\\[A-Za-z@]+\*?", " ", s).replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def _links(raw: str) -> list[str]:
    links: list[str] = []
    replace_command(raw, "url", lambda a: links.append(a[0]) or "")
    replace_command(raw, "href", lambda a: links.append(a[0]) or "", nargs=2)
    links.extend(_URL_TEXT.findall(raw))
    return links


def _first_group(src: str, command: str) -> str:
    found: list[str] = []
    replace_command(src, command, lambda a: found.append(a[0]) or "")
    return found[0] if found else ""


def _authors(raw: str) -> list[str]:
    raw = replace_command(raw, "thanks", lambda _a: "")
    raw = replace_command(raw, "footnote", lambda _a: "")
    raw = replace_command(raw, "footnotemark", lambda _a: "", nargs=0)
    raw = re.sub(r"\$\^\{?[^$]*\}?\$|\^\{[^}]*\}", "", raw)  # affiliation marks like $^*$
    names: list[str] = []
    for chunk in re.split(r"\\(?:and|AND|And)(?![A-Za-z])", raw):
        first = re.split(r"\\\\", chunk)[0]
        for name in render(first, {}).split(","):
            name = re.sub(r"[*\d]+$", "", name).strip()
            if name and len(name) < 80:
                names.append(name)
    return names


# -- entry point --------------------------------------------------------------------------


def decode_files(raw: dict[str, bytes]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, data in raw.items():
        if not name.lower().endswith((".tex", ".bbl", ".ltx")):
            continue
        try:
            out[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            out[name] = data.decode("latin-1")
    return out


class NoLatexSource(ValueError):
    """No main .tex file in the source bundle."""


def parse_latex(raw_files: dict[str, bytes], *, paper_id: str, ref: PaperRef) -> PaperDoc:
    files = decode_files(raw_files)
    main = find_main(files)
    if main is None:
        raise NoLatexSource("No main .tex file found in the paper source")

    src = flatten(main, files)
    src = _COMMENT_ENV.sub("", _IFFALSE.sub("", src))
    src, macros = collect_macros(src)
    src = expand_macros(src, macros)

    m = re.search(r"\\begin\s*\{document\}", src)
    preamble, body = (src[: m.start()], src[m.end() :]) if m else ("", src)
    end = re.search(r"\\end\s*\{document\}", body)
    body = body[: end.start()] if end else body

    title = render(_first_group(src, "title"), {})
    authors = _authors(_first_group(src, "author"))
    abstract_m = re.search(r"\\begin\s*\{abstract\}(.*?)\\end\s*\{abstract\}", body, re.S)
    abstract_raw = abstract_m.group(1) if abstract_m else _first_group(body, "abstract")
    # Front-matter commands are not paragraphs.
    body = re.sub(r"\\(?:maketitle|tableofcontents|clearpage|newpage)\b", "", body)
    for command in ("title", "author", "date", "affiliation", "institute", "email"):
        body = replace_command(body, command, lambda _a: "")

    events, bib = walk(body)
    eq_within = bool(re.search(r"\\numberwithin\s*\{equation\}\s*\{section\}", preamble))
    labels = number_events(events, eq_within_section=eq_within)

    b = DocBuilder()
    for link in _links(abstract_raw):
        b.add_link(link)
    for ev in events:
        if isinstance(ev, AppendixEv):
            b.start_appendix()
        elif isinstance(ev, SectionEv):
            anchor = b.section(ev.level, ev.number, render(ev.title, labels))
            for key in ev.labels:
                b.register_key(key, anchor.id)
        elif isinstance(ev, FloatEv):
            anchor = b.float(ev.kind, render(ev.caption, labels), ev.number)
            for key in ev.labels:
                b.register_key(key, anchor.id)
            for link in _links(ev.caption):
                b.add_link(link)
        else:
            raw_text = " ".join(p if isinstance(p, str) else " " for p in ev.parts)
            para = b.paragraph(render(raw_text, labels))
            if para is not None:
                for refm in _REF.finditer(raw_text):
                    for key in refm["keys"].split(","):
                        b.add_ref(para.id, key.strip())
            for link in _links(raw_text):
                b.add_link(link)
            for part in ev.parts:
                if isinstance(part, EqEv):
                    eq = b.equation(part.latex, part.number, parent=para)
                    for key in part.labels:
                        b.register_key(key, eq.id)

    if not bib:
        for name, text in files.items():
            if name.endswith(".bbl"):
                m2 = re.search(
                    r"\\begin\{thebibliography\}.*?\n(.*)\\end\{thebibliography\}", text, re.S
                )
                if m2:
                    bib = _bibitems(m2.group(1))
                    break

    return b.build(
        paper_id=paper_id,
        ref=ref.model_copy(update={"source_kind": "arxiv_latex"}),
        title=title,
        authors=authors,
        abstract=render(abstract_raw, labels),
        references=bib,
    )
