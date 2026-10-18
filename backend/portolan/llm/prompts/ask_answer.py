"""`ask_answer` (spec §10.10, the Ask box): answer one question from retrieved sources only.

Inputs:
    {"paper_title": str,
     "question": str,
     "passages": [{"id": str, "label": str, "text": str}, ...],      # ≤ 5, from the paper
     "code": [{"path": str, "start_line": int, "end_line": int, "qualname": str,
               "lines": [[int, str], ...]}, ...],                    # ≤ 3, from the repo
     "faq": [{"id": str, "question": str, "answer": str}, ...]}      # ≤ 3 stored FAQ items
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.faq_schemas import AskOut
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

PASSAGES = 5
PASSAGE_CHARS = 1000
CODE = 3
CODE_LINES = 40
LINE_CHARS = 200
FAQ = 3
FAQ_CHARS = 700
SCHEMA_TEXT = json.dumps(AskOut.model_json_schema(), indent=2)

EXAMPLE = AskOut(
    supported=True,
    answer="The paper divides by the square root of the key size so the scores don't grow with "
    "the dimension and saturate softmax (Eq. 1).",
    anchors=["eq:1"],
    faq=[],
    code=["model/attention.py:38-45"],
)

SYSTEM = f"""\
You answer one reader's question about a research paper using ONLY the sources provided: passages \
of the paper, code from its repository, and earlier FAQ answers.

Rules:
- If the sources do not answer the question, set supported to false and leave answer empty. Never \
guess and never use outside knowledge.
- If supported: answer in 40-150 words, plain words, paraphrased; use $...$ for math if needed.
- anchors: ids of the passages you used; faq: ids of the FAQ items you used; code: the code spans \
you used, written exactly as "path:start-end" from the given spans. Cite at least one.
- Use only ids and spans that appear in the sources.

Output JSON only, matching this schema exactly:
```json
{SCHEMA_TEXT}
```

Example of the format:
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


def _code(spans: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for s in spans[:CODE]:
        lines = "\n".join(f"{n:>5}  {text[:LINE_CHARS]}" for n, text in s["lines"][:CODE_LINES])
        blocks.append(f"{s['path']}:{s['start_line']}-{s['end_line']}  {s['qualname']}\n{lines}")
    return "\n\n".join(blocks) or "(no code)"


def _faq(items: list[dict[str, Any]]) -> str:
    blocks = [
        f"[{i['id']}] Q: {i['question']}\nA: {str(i['answer'])[:FAQ_CHARS]}" for i in items[:FAQ]
    ]
    return "\n\n".join(blocks) or "(none)"


def render(inputs: dict[str, Any]) -> list[Message]:
    user = (
        f"Paper: {inputs['paper_title']}\n\nQuestion: {defang(str(inputs['question']))}\n\n"
        f"{untrusted('paper', defang(_passages(inputs['passages'])))}\n\n"
        f"{untrusted('code', defang(_code(inputs['code'])))}\n\n"
        f"{untrusted('faq', defang(_faq(inputs['faq'])))}\n\n"
        "Return the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="ask_answer",
    version=1,
    render=render,
    schema=AskOut,
    temperature=0.2,
    reasoning="low",
)
