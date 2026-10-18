"""`ground_code` (spec §10.7.2, Appendix D.4): which code implements a concept, with line notes.

Inputs:
    {"paper_title": str,
     "concept": {"title": str, "gist": str},
     "passages": [{"id": str, "label": str, "text": str}, ...],     # ≤ 4 anchors
     "symbols": {str: str},                                         # the paper's notation
     "candidates": [{"path": str, "start_line": int, "end_line": int, "qualname": str,
                     "lines": [[int, str], ...]}, ...],             # retrieved code, numbered
     "library_modules": [str, ...],                                 # third-party imports
     "error": str | None}                                           # why the last try was refused
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.ground_schemas import GroundOut, NoteOut, SpanOut
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

PASSAGES = 4
PASSAGE_CHARS = 1200
CANDIDATES = 6
CANDIDATE_LINES = 60
LINE_CHARS = 200
SCHEMA_TEXT = json.dumps(GroundOut.model_json_schema(), indent=2)

EXAMPLE = GroundOut(
    grounding="in_code",
    spans=[SpanOut(path="attention.py", start_line=38, end_line=45)],
    notes=[
        NoteOut(
            line=41,
            tag="Eq. 1 · QKᵀ/√dₖ",
            note="Scores every query against every key → (…, seq, seq).",
            kind="implements",
        ),
        NoteOut(
            line=44,
            tag="softmax",
            note="torch subtracts the row max first; the paper doesn't mention it.",
            kind="detail_not_in_paper",
        ),
    ],
    library_calls=["torch.softmax"],
    summary="Lines 38-45 implement Eq. 1.",
)

SYSTEM = f"""\
You are connecting one concept from a research paper to the code in the paper's repository. A \
reader will see the lines you pick highlighted, with a short note under the lines you annotate.

Choose:
- spans: the smallest line ranges (at most 60 lines each) that implement the concept. Use only \
paths and line numbers that appear in the candidate code, exactly as shown.
- notes: 2 to 8 line notes, each on a line inside one of your spans.
  - tag: the part of the paper the line implements ("Eq. 1 · QKᵀ/√dₖ", "§3.2.3", "Table 3").
  - note: what the line does, at most 25 words; give tensor shapes where they help.
  - kind: "implements" (the line does what the paper describes); "detail_not_in_paper" (the code \
does something the paper never mentions); "mismatch" (only when a passage states a specific value \
or step and the code visibly differs; quote neither, name both sides); "context" (setup the \
reader needs).
- library_calls: fully qualified library functions that do the work ("torch.softmax"), using the \
names of the listed library modules.
- grounding: "in_code" when spans implement the concept; "in_library" when only a library call \
does (spans stay empty); "concept_only" when the code does not spell the idea out (spans, notes \
and library_calls stay empty).
- summary: one sentence a reader sees above the code ("Lines 38-45 implement Eq. 1.").

Rules:
- Never invent a path or a line number. If nothing fits, answer concept_only.
- Judge only the code and passages given. Don't guess what other files contain.

Output JSON only, matching this schema exactly:
```json
{SCHEMA_TEXT}
```

Example of the format (shorter than yours):
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def _passages(passages: list[dict[str, Any]]) -> str:
    blocks = [
        f"[{p['id']}] {p.get('label', '')}\n{str(p['text'])[:PASSAGE_CHARS]}"
        for p in passages[:PASSAGES]
    ]
    return "\n\n".join(blocks) or "(no passages)"


def _candidate(c: dict[str, Any]) -> str:
    head = (
        f"### {c['path']}  lines {c['start_line']}-{c['end_line']}  ({c['qualname'] or 'module'})"
    )
    body = "\n".join(
        f"{number:>5}| {str(text)[:LINE_CHARS]}" for number, text in c["lines"][:CANDIDATE_LINES]
    )
    return f"{head}\n{body}"


def render(inputs: dict[str, Any]) -> list[Message]:
    concept = inputs["concept"]
    symbols: dict[str, str] = inputs.get("symbols") or {}
    notation = "\n".join(f"{k}: {v}" for k, v in list(symbols.items())[:30]) or "(none extracted)"
    paper = (
        f"Paper: {inputs['paper_title']}\n\nPassages:\n{_passages(inputs['passages'])}\n\n"
        f"Notation in the paper:\n{notation}"
    )
    code = "\n\n".join(_candidate(c) for c in inputs["candidates"][:CANDIDATES]) or "(no code)"
    libraries = ", ".join(inputs.get("library_modules") or []) or "(none)"
    retry = ""
    if inputs.get("error"):
        retry = (
            f"\n\nYour previous answer was refused: {inputs['error']} "
            "Choose again, using only the paths and line numbers shown below."
        )
    user = (
        f"Concept: {concept['title']}\nGist on the map: {concept['gist']}\n"
        f"Library modules used by the repository: {libraries}\n\n"
        f"{untrusted('paper', defang(paper))}\n\n"
        f"Candidate code (line numbers are on the left):\n{untrusted('code', defang(code))}"
        f"{retry}\n\nReturn the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="ground_code",
    version=1,
    render=render,
    schema=GroundOut,
    temperature=0.2,
    reasoning="medium",
)
