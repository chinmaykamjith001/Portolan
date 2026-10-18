"""`reverse_scan` (spec §10.9.4): behavior in the code that the paper never describes.

Inputs:
    {"paper_title": str,
     "described": [str, ...],                 # short list of what the paper's method says
     "snippets": [{"path": str, "start_line": int, "end_line": int, "qualname": str,
                   "lines": [[int, str], ...]}, ...]}
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.master_layer import defang
from portolan.llm.prompts.verify_schemas import ReverseOut, UndocumentedOut
from portolan.llm.types import Message

CANDIDATE_LINES = 40
LINE_CHARS = 180
DESCRIBED = 40
SCHEMA_TEXT = json.dumps(ReverseOut.model_json_schema(), indent=2)

EXAMPLE = ReverseOut(
    items=[
        UndocumentedOut(
            title="Gradient clipping at 1.0",
            evidence="train.py clips the gradient norm to 1.0 before each step; the paper lists "
            "no clipping.",
            path="train.py",
            start_line=33,
            end_line=33,
            severity="low",
            confidence=0.8,
        )
    ]
)

SYSTEM = f"""\
You compare a research paper's description of its method with the code that implements it, and \
report behavior in the code that changes results but is NOT described in the paper (for example \
gradient clipping, an extra normalization, a scaling constant, a learning-rate schedule detail).

Rules:
- Report only behavior that appears in the snippets and is not covered by the list of what the \
paper describes.
- severity "medium" when it plausibly changes the results; otherwise "low". Never higher.
- Give path, start_line and end_line exactly as shown. Never invent a path or a line number.
- At most 6 items. Report nothing if nothing qualifies; an empty list is a good answer.
- confidence from 0 to 1, honestly.

Output JSON only, matching this schema exactly:
```json
{SCHEMA_TEXT}
```

Example of the format:
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def render(inputs: dict[str, Any]) -> list[Message]:
    described = "\n".join(f"- {d}" for d in inputs["described"][:DESCRIBED]) or "(nothing listed)"
    code = "\n\n".join(
        f"### {c['path']}  lines {c['start_line']}-{c['end_line']}  ({c['qualname'] or 'module'})\n"
        + "\n".join(f"{n:>5}| {str(t)[:LINE_CHARS]}" for n, t in c["lines"][:CANDIDATE_LINES])
        for c in inputs["snippets"]
    )
    user = (
        f"Paper: {inputs['paper_title']}\n\nWhat the paper describes:\n"
        f"{untrusted('paper', defang(described))}\n\nCode:\n{untrusted('code', defang(code))}"
        "\n\nReturn the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="reverse_scan",
    version=1,
    render=render,
    schema=ReverseOut,
    temperature=0.1,
    reasoning="medium",
)
