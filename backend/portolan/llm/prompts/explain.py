"""`explain` (spec §10.7.1, Appendix D.3): the default explanation shown in the drawer.

Inputs:
    {"paper_title": str,
     "concept": {"title": str, "gist": str},
     "path": [str, ...],               # titles from the master center down to this concept
     "layer_center": str,              # title of the layer the reader is looking at
     "passages": [{"id": str, "label": str, "text": str}, ...],  # ≤ 4 anchors, ≤ 1,200 chars
     "symbols": {str: str}}            # the paper's notation table (may be empty)
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.detail_schemas import ExplainOut, ExplainSections
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

PASSAGES = 4
PASSAGE_CHARS = 1200
SCHEMA_TEXT = json.dumps(ExplainOut.model_json_schema(), indent=2)

EXAMPLE = ExplainOut(
    gist="Softmax turns a row of scores into positive weights that sum to 1.",
    sections=ExplainSections(
        what_it_is="Softmax is a way to turn arbitrary scores into a probability-like set of "
        "weights: bigger scores get bigger shares, every share is positive, and the shares add "
        "up to one.",
        how_it_works="Exponentiate each score, then divide by the total: "
        "$\\mathrm{softmax}(z)_i = e^{z_i} / \\sum_j e^{z_j}$. Exponentiation makes every value "
        "positive and exaggerates differences; dividing by the sum normalizes.",
        why_its_here="Under attention, the scores are query-key similarities, so softmax decides "
        "how much each position contributes to the output (Eq. 1).",
        where_it_appears=["eq:1"],
        common_confusion="It is not a hard choice of the maximum: every key keeps some weight, "
        "which is why attention can blend information from several positions.",
    ),
)

SYSTEM = f"""\
You are writing the explanation a reader sees when they open one concept on a concept map of a \
research paper. The explanation must actually explain: 200-400 words in total across the sections.

Write each section:
- gist: one sentence, the whole idea.
- what_it_is: intuition first, in plain words.
- how_it_works: step by step. Use the paper's notation in KaTeX ($...$) where it helps; \
define each symbol you use.
- why_its_here: the role it plays in THIS paper and under the concept the reader came from (the \
last titles of the path). The path changes the framing: "Softmax" under attention means weights \
over keys; under a classifier head it means probabilities over classes.
- where_it_appears: ids of the given passages that discuss it (only ids from the input).
- common_confusion: one common misconception and its correction.

Rules:
- Paraphrase the paper; quote at most a short phrase or an equation.
- Don't invent numbers, results or claims that aren't in the passages.
- Plain sentences, no lists inside sections, no headings, no Markdown except $...$ math.

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


def render(inputs: dict[str, Any]) -> list[Message]:
    concept = inputs["concept"]
    symbols: dict[str, str] = inputs.get("symbols") or {}
    notation = "\n".join(f"{k}: {v}" for k, v in list(symbols.items())[:30]) or "(none extracted)"
    paper = (
        f"Paper: {inputs['paper_title']}\n\nPassages:\n{_passages(inputs['passages'])}\n\n"
        f"Notation in the paper:\n{notation}"
    )
    user = (
        f"Concept: {concept['title']}\nGist on the map: {concept['gist']}\n"
        f"Path from the paper's core idea: {' > '.join(inputs['path'])}\n"
        f"The reader is looking at the layer centered on: {inputs['layer_center']}\n\n"
        f"{untrusted('paper', defang(paper))}\n\n"
        "Return the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="explain",
    version=1,
    render=render,
    schema=ExplainOut,
    temperature=0.5,
    reasoning="low",
)
