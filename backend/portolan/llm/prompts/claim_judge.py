"""`claim_judge` (spec §10.9.3): does the code do what the paper claims?

Inputs:
    {"paper_title": str,
     "items": [{"key": str,
                "claim": str, "claim_type": str, "subject": str, "value": str | None,
                "passage": {"id": str, "label": str, "text": str},
                "candidates": [{"path": str, "start_line": int, "end_line": int,
                                "qualname": str, "lines": [[int, str], ...]}, ...]}, ...]}
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.master_layer import defang
from portolan.llm.prompts.verify_schemas import JudgeOut, VerdictOut
from portolan.llm.types import Message

PASSAGE_CHARS = 900
CANDIDATE_LINES = 40
LINE_CHARS = 180
SCHEMA_TEXT = json.dumps(JudgeOut.model_json_schema(), indent=2)

EXAMPLE = JudgeOut(
    verdicts=[
        VerdictOut(
            key="c1",
            verdict="mismatch",
            confidence=0.9,
            paper_side="scores are divided by sqrt(d_k)",
            code_side="attention.py divides by d_k, with no square root",
            path="attention.py",
            start_line=12,
            end_line=12,
        )
    ]
)

SYSTEM = f"""\
You compare claims from a research paper with the code that is supposed to implement them, and \
decide for each claim whether the code matches.

For every item return one verdict:
- "matches": the code does what the claim says.
- "mismatch": the code clearly does something different from what the claim says. Use this only \
when you can point at the exact lines AND state both sides.
- "uncertain": you cannot tell from the code shown (other files may matter).
- "not_found": none of the code shown relates to the claim.

Also give:
- confidence from 0 to 1, honestly. A mismatch you are not sure about must have low confidence.
- paper_side and code_side: a few plain words each. A mismatch needs both.
- path, start_line and end_line: the lines your verdict rests on, exactly as shown. Never invent \
a path or a line number.

Judge only the code shown. Different names, formatting or equivalent forms of the same value \
(0.1 and 1e-1, d_model // h and 64) are not mismatches.

Output JSON only, matching this schema exactly:
```json
{SCHEMA_TEXT}
```

Example of the format:
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def _item(i: dict[str, Any]) -> str:
    p = i["passage"]
    value = f" (value: {i['value']})" if i.get("value") else ""
    head = (
        f"## key {i['key']} · {i['claim_type']} · {i['subject']}{value}\n"
        f"Claim: {i['claim']}\nPaper passage [{p['id']}] {p.get('label', '')}: "
        f"{str(p['text'])[:PASSAGE_CHARS]}"
    )
    code = "\n\n".join(
        f"### {c['path']}  lines {c['start_line']}-{c['end_line']}  ({c['qualname'] or 'module'})\n"
        + "\n".join(f"{n:>5}| {str(t)[:LINE_CHARS]}" for n, t in c["lines"][:CANDIDATE_LINES])
        for c in i["candidates"]
    )
    return f"{head}\n\nCode:\n{code or '(no code found)'}"


def render(inputs: dict[str, Any]) -> list[Message]:
    body = "\n\n---\n\n".join(_item(i) for i in inputs["items"])
    user = (
        f"Paper: {inputs['paper_title']}\n\nItems:\n{untrusted('claims', defang(body))}"
        "\n\nReturn the JSON object only, one verdict per key."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="claim_judge",
    version=1,
    render=render,
    schema=JudgeOut,
    temperature=0.1,
    reasoning="medium",
)
