"""`faq_answer` (spec §10.10): grounded answers for clusters of collected questions.

Inputs:
    {"paper_title": str,
     "clusters": [{"index": int,
                   "kind": "reviewer_discussion" | "github_issue",
                   "question": str,
                   "source_text": str,                       # what the source said (untrusted)
                   "passages": [{"id": str, "label": str, "text": str}, ...]}, ...]}  # ≤ 3 each
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.faq_schemas import AnswerBatchOut, ClusterAnswerOut
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

CLUSTERS = 6
PASSAGES = 3
PASSAGE_CHARS = 900
SOURCE_CHARS = 1500
SCHEMA_TEXT = json.dumps(AnswerBatchOut.model_json_schema(), indent=2)

EXAMPLE = AnswerBatchOut(
    items=[
        ClusterAnswerOut(
            cluster=0,
            keep=True,
            question="Why are the dot products divided by the square root of the key size?",
            answer="Without the division, the scores grow with the key dimension, which pushes "
            "softmax into regions where its gradient is tiny. Dividing by the square root keeps "
            "their variance near one, so training stays stable (Eq. 1).",
            anchors=["eq:1"],
        )
    ]
)

SYSTEM = f"""\
You are writing the FAQ of a research paper. Each cluster below is a question that real readers \
asked (a reviewer, or a user of the paper's code), the reply the source gave, and passages from \
the paper. Write one short answer per cluster.

For each cluster:
- question: one clear standalone sentence; no names, no "the reviewer asked".
- answer: 40-120 words, plain words. Paraphrase the paper and the source's reply; never copy long \
text. Use $...$ for math if needed. Don't invent numbers, results or claims that the passages and \
the reply don't contain.
- anchors: ids of the given passages the answer rests on (only ids from that cluster).
- keep: false when neither the reply nor the passages let you answer. Don't guess.

Output JSON only, matching this schema exactly (one entry per cluster, `cluster` = its index):
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
    blocks: list[str] = []
    for c in inputs["clusters"][:CLUSTERS]:
        blocks.append(
            f"Cluster {c['index']} ({c['kind']})\nQuestion: {c['question']}\n"
            f"{untrusted(c['kind'], defang(str(c['source_text'])[:SOURCE_CHARS]))}\n"
            f"{untrusted('paper', defang(_passages(c['passages'])))}"
        )
    user = f"Paper: {inputs['paper_title']}\n\n" + "\n\n".join(blocks) + "\n\nReturn the JSON only."
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="faq_answer",
    version=1,
    render=render,
    schema=AnswerBatchOut,
    temperature=0.3,
    reasoning="low",
)
