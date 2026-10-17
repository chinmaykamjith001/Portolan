"""`edge_explain` (spec §10.8, Appendix D.6): the popover for a double-clicked link.

Inputs:
    {"paper_title": str,
     "source": {"title": str, "gist": str},
     "target": {"title": str, "gist": str},
     "label": str,                     # the link label, e.g. "is order-blind, so needs"
     "layer_center": str,
     "path": [str, ...],
     "passages": [{"id": str, "label": str, "text": str}, ...]}  # ≤ 4, ≤ 1,000 chars each
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.detail_schemas import EdgeOut
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

PASSAGES = 4
PASSAGE_CHARS = 1000
SCHEMA_TEXT = json.dumps(EdgeOut.model_json_schema(), indent=2)

EXAMPLE = EdgeOut(
    headline="Self-attention needs positional encoding",
    body="Self-attention compares every position with every other one using only the content "
    "of the tokens, so shuffling the input would produce the same set of comparisons. Nothing in "
    "the computation says which token came first. Positional encodings add a position-dependent "
    "signal to each token's embedding before attention runs, so the comparisons can depend on "
    "order as well as content.",
    why_it_matters="Without it the model could not tell 'dog bites man' from 'man bites dog'.",
)

SYSTEM = f"""\
You explain one labeled link on a concept map of a research paper. The link reads as a sentence: \
SOURCE — label → TARGET. Explain why that relationship holds, in more depth than the label.

- headline: the relationship restated as one short sentence.
- body: 80-150 words on WHY it holds in this paper: the mechanism, not a definition of each side.
- why_it_matters: one sentence on what would go wrong or be missing without it.

Paraphrase the paper; don't invent numbers or results. Plain prose; $...$ math is allowed.

Output JSON only, matching this schema exactly:
```json
{SCHEMA_TEXT}
```

Example:
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def render(inputs: dict[str, Any]) -> list[Message]:
    passages = (
        "\n\n".join(
            f"[{p['id']}] {p.get('label', '')}\n{str(p['text'])[:PASSAGE_CHARS]}"
            for p in inputs["passages"][:PASSAGES]
        )
        or "(no passages)"
    )
    s, t = inputs["source"], inputs["target"]
    paper = f"Paper: {inputs['paper_title']}\n\nPassages:\n{passages}"
    user = (
        f"Link: {s['title']} — {inputs['label']} → {t['title']}\n"
        f"{s['title']}: {s['gist']}\n{t['title']}: {t['gist']}\n"
        f"Layer center: {inputs['layer_center']}\nPath: {' > '.join(inputs['path'])}\n\n"
        f"{untrusted('paper', defang(paper))}\n\nReturn the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="edge_explain",
    version=1,
    render=render,
    schema=EdgeOut,
    temperature=0.5,
    reasoning="low",
)
