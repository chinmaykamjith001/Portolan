"""`faq_anticipated` (spec §10.10 step 5): questions a newcomer would ask about confusing concepts.

Inputs:
    {"paper_title": str,
     "concepts": [{"index": int, "title": str, "gist": str,
                   "passages": [{"id": str, "label": str, "text": str}, ...]}, ...]}  # ≤ 3 each
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.faq_schemas import AnticipatedBatchOut, AnticipatedOut
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

CONCEPTS = 6
PASSAGES = 3
PASSAGE_CHARS = 900
SCHEMA_TEXT = json.dumps(AnticipatedBatchOut.model_json_schema(), indent=2)

EXAMPLE = AnticipatedBatchOut(
    items=[
        AnticipatedOut(
            concept=0,
            question="Why does attention need positional encoding?",
            answer="Attention treats its input as a set: shuffling the tokens would give the same "
            "output. Adding a position signal to each token lets the model tell order apart.",
            anchors=["sec:3.5"],
        )
    ]
)

SYSTEM = f"""\
You are writing the FAQ of a research paper. For each concept below, write the one question a \
newcomer would most plausibly ask about it (a misconception, a "why", or a "how does that differ \
from ..."), and a grounded answer.

Rules:
- question: one standalone sentence.
- answer: 40-120 words, plain words, paraphrasing the passages; use $...$ for math if needed. \
Don't invent numbers, results or claims that the passages don't contain.
- anchors: ids of the given passages the answer rests on (only ids from that concept's passages). \
If the passages don't support an answer, skip that concept.

Output JSON only, matching this schema exactly (`concept` = the concept's index):
```json
{SCHEMA_TEXT}
```

Example of the format:
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def render(inputs: dict[str, Any]) -> list[Message]:
    blocks: list[str] = []
    for c in inputs["concepts"][:CONCEPTS]:
        passages = "\n\n".join(
            f"[{p['id']}] {p.get('label', '')}\n{str(p['text'])[:PASSAGE_CHARS]}"
            for p in c["passages"][:PASSAGES]
        )
        blocks.append(
            f"Concept {c['index']}: {c['title']}\nGist: {c['gist']}\n"
            f"{untrusted('paper', defang(passages or '(no passages)'))}"
        )
    user = f"Paper: {inputs['paper_title']}\n\n" + "\n\n".join(blocks) + "\n\nReturn the JSON only."
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="faq_anticipated",
    version=1,
    render=render,
    schema=AnticipatedBatchOut,
    temperature=0.4,
    reasoning="low",
)
