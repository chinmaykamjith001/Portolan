"""`example_program` (spec §10.7.3, Appendix D.5): a tiny program that computes a worked example.

The numbers the reader sees come from running the program in the sandbox, never from this reply.

Inputs:
    {"paper_title": str,
     "concept": {"title": str, "gist": str},
     "path": [str, ...],               # titles from the master center down to this concept
     "passages": [{"id": str, "label": str, "text": str}, ...],  # <= 4 anchors, <= 1,200 chars
     "symbols": {str: str},            # the paper's notation table (may be empty)
     "variant": int,                   # 0 = typical case; > 0 = a different, edge-case example
     "previous": [{"caption": str, "program": str, "columns": [str], "rows": [[str]]}, ...],
     "retry": {"program": str, "error": str}}   # only after a failed attempt
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.master_layer import defang
from portolan.llm.prompts.program_schemas import ExampleOut
from portolan.llm.types import Message

PASSAGES = 4
PASSAGE_CHARS = 1200
PREVIOUS = 3
PREVIOUS_ROWS = 4
SCHEMA_TEXT = json.dumps(ExampleOut.model_json_schema(), indent=2)

EXAMPLE = ExampleOut(
    caption="softmax([2.0, 1.0, 0.1])",
    source_label="paper_notation",
    program=(
        "import numpy as np\n"
        "import json\n"
        "z = np.array([2.0, 1.0, 0.1])\n"
        "p = np.exp(z - z.max()); p /= p.sum()\n"
        'print(json.dumps({"columns": ["score", "weight"], '
        '"rows": [[f"{a:.1f}", f"{b:.3f}"] for a, b in zip(z, p)]}))'
    ),
    note="The weights sum to 1.",
)

SYSTEM = f"""\
You are writing a worked example for one concept of a research paper. You write a SMALL Python \
program; the program is run in a sandbox and its printed table is what the reader sees. You never \
type the result yourself.

The program:
- is at most 40 lines and prints exactly ONE line of JSON: \
{{"columns": [...], "rows": [[...], ...]}} with at most 8 columns and 12 rows;
- formats every cell as a short string (f-strings, e.g. f"{{x:.3f}}") so the table reads cleanly;
- uses the paper's notation and realistic-but-small sizes where possible (a 3-element vector, a \
2x2 matrix), and is deterministic (no randomness, or a fixed seed);
- may import ONLY math, statistics, fractions, json and numpy;
- must not read or write files, use the network, start processes, or call open, exec, eval, \
compile or __import__, and must not touch names that start with two underscores.

Fields:
- caption: what the example computes, in a few plain words. Don't put results in it.
- source_label: "paper_notation" if the example uses symbols or sizes from the paper, otherwise \
"generic".
- note: one sentence on what to notice, or null. Don't state numbers the table doesn't show; \
any number in the note that the run doesn't produce is discarded.

When asked for a different case than the earlier examples, choose a clearly different input, \
preferably an edge case that shows why the concept is subtle (for example softmax on large \
numbers, where a naive version overflows and libraries subtract the maximum first).

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


def _previous(previous: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for i, p in enumerate(previous[-PREVIOUS:], 1):
        table = json.dumps(
            {"columns": p["columns"], "rows": p["rows"][:PREVIOUS_ROWS]}, ensure_ascii=False
        )
        blocks.append(
            f"Earlier example {i}: {p['caption']}\n```python\n{p['program']}\n```\n{table}"
        )
    return "\n\n".join(blocks)


def render(inputs: dict[str, Any]) -> list[Message]:
    concept = inputs["concept"]
    symbols: dict[str, str] = inputs.get("symbols") or {}
    notation = "\n".join(f"{k}: {v}" for k, v in list(symbols.items())[:30]) or "(none extracted)"
    paper = (
        f"Paper: {inputs['paper_title']}\n\nPassages:\n{_passages(inputs['passages'])}\n\n"
        f"Notation in the paper:\n{notation}"
    )
    variant = int(inputs.get("variant", 0))
    case = (
        "Show a typical case, small enough to read at a glance."
        if variant == 0
        else "Show a DIFFERENT case from the earlier examples below, preferably an edge case."
    )
    parts = [
        f"Concept: {concept['title']}\nGist on the map: {concept['gist']}\n"
        f"Path from the paper's core idea: {' > '.join(inputs['path'])}\n{case}"
    ]
    if inputs.get("previous"):
        parts.append(_previous(inputs["previous"]))
    parts.append(untrusted("paper", defang(paper)))
    if inputs.get("retry"):
        retry = inputs["retry"]
        parts.append(
            "Your previous program failed.\n"
            f"```python\n{retry['program']}\n```\n"
            f"Problem: {retry['error']}\nFix it and answer again."
        )
    parts.append("Return the JSON object only.")
    return [
        Message(role="system", content=SYSTEM),
        Message(role="user", content="\n\n".join(parts)),
    ]


TEMPLATE = PromptTemplate(
    id="example_program",
    version=1,
    render=render,
    schema=ExampleOut,
    temperature=0.3,
    reasoning="low",
)
