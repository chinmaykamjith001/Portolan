"""`graph_program` (spec §10.7.4): pick a graph template and write the program that computes it.

The data plotted comes from running the program in the sandbox. `template: "none"` means no
visual would genuinely help, and the drawer hides its Graph tab.

Inputs:
    {"paper_title": str,
     "concept": {"title": str, "gist": str},
     "path": [str, ...],
     "passages": [{"id": str, "label": str, "text": str}, ...],
     "symbols": {str: str},
     "retry": {"program": str, "error": str}}   # only after a failed attempt
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.example_program import PASSAGE_CHARS, PASSAGES
from portolan.llm.prompts.master_layer import defang
from portolan.llm.prompts.program_schemas import GraphOut
from portolan.llm.types import Message

SCHEMA_TEXT = json.dumps(GraphOut.model_json_schema(), indent=2)

# The per-template JSON the program prints. Keep in sync with the docstring of detail/graph.py.
SHAPES = """\
bar:          {"x": ["a", "b"], "series": [{"name": "s", "y": [1.0, 2.0]}]}
line:         {"x": [0, 1, 2], "series": [{"name": "s", "y": [1.0, 2.0, 3.0]}]}
heatmap:      {"rows": ["r1", "r2"], "cols": ["c1", "c2"], "values": [[1.0, 2.0], [3.0, 4.0]]}
before_after: {"labels": ["a", "b"], "before": [1.0, 2.0], "after": [1.5, 1.0]}
distribution: {"values": [0.1, 0.4, 0.4, 0.9], "bins": 10}
              or {"x": [0, 1, 2], "y": [0.2, 0.5, 0.3]}"""

EXAMPLE = GraphOut(
    template="line",
    caption="Attention weight on one key as its score grows",
    program=(
        "import math\n"
        "import json\n"
        "scores = [i / 2 for i in range(-6, 7)]\n"
        "w = [math.exp(s) / (math.exp(s) + 2) for s in scores]\n"
        'print(json.dumps({"x": scores, "series": [{"name": "weight", "y": w}], '
        '"x_label": "score", "y_label": "weight"}))'
    ),
    note="The curve flattens out: extra score stops adding weight.",
)

SYSTEM = f"""\
You are deciding whether one concept of a research paper deserves a small graph, and if so \
writing the program that computes it. The program is run in a sandbox; the plotted data is what \
it prints. You never type data points yourself.

Choose "none" (and leave caption, program and note empty) unless a graph would genuinely teach \
something that words and a table can't: a function's shape, a comparison before/after a change, \
a distribution, a matrix of weights. Most abstract or definitional concepts get "none".

Templates and the JSON the program must print (exactly one line, via json.dumps):
{SHAPES}
Every template also accepts optional "x_label" and "y_label" strings. before_after also accepts \
"before_name" and "after_name". Use at most 6 series, 60 bars, 200 line points and a 24x24 \
heatmap. All numbers must be finite.

The program:
- is at most 40 lines, deterministic (no randomness, or a fixed seed), with small sizes;
- may import ONLY math, statistics, fractions, json and numpy;
- must not read or write files, use the network, start processes, or call open, exec, eval, \
compile or __import__, and must not touch names that start with two underscores.

Fields:
- caption: what the graph shows, in plain words. Don't put results in it.
- note: one sentence on what to notice, or null. Any number in the note that the run doesn't \
produce is discarded.

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


def render(inputs: dict[str, Any]) -> list[Message]:
    concept = inputs["concept"]
    symbols: dict[str, str] = inputs.get("symbols") or {}
    notation = "\n".join(f"{k}: {v}" for k, v in list(symbols.items())[:30]) or "(none extracted)"
    paper = (
        f"Paper: {inputs['paper_title']}\n\nPassages:\n{_passages(inputs['passages'])}\n\n"
        f"Notation in the paper:\n{notation}"
    )
    parts = [
        f"Concept: {concept['title']}\nGist on the map: {concept['gist']}\n"
        f"Path from the paper's core idea: {' > '.join(inputs['path'])}",
        untrusted("paper", defang(paper)),
    ]
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
    id="graph_program",
    version=1,
    render=render,
    schema=GraphOut,
    temperature=0.2,
    reasoning="low",
)
