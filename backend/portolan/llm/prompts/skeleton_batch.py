"""`skeleton_batch` (spec §10.5.2, Appendix D.2): sub-concept skeletons for a batch of concepts.

Inputs:
    {"paper_title": str,
     "paper_summary": str,          # ≤ 600 chars (clipped here if longer)
     "min_children": int,           # 4
     "max_children": int,           # 10
     "items": [{"key": str,         # echoed back as center_key
                "title": str, "gist": str,
                "path": [str, ...], # titles from the master center down to this item, inclusive
                "anchor_texts": [str, ...]}]}  # ≤ 3 short passages (extra ones are dropped)
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.chart_schemas import (
    ConceptOut,
    LinkOut,
    SkeletonBatchOut,
    SkeletonLayerOut,
)
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

SUMMARY_CHARS = 600
PASSAGES = 3
PASSAGE_CHARS = 600

SCHEMA_TEXT = json.dumps(SkeletonBatchOut.model_json_schema(), indent=2)

# Abbreviated: a real layer has min_children..max_children sub-concepts.
EXAMPLE = SkeletonBatchOut(
    layers=[
        SkeletonLayerOut(
            center_key="softmax",
            concepts=[
                ConceptOut(
                    key="exponential",
                    title="Exponential function",
                    gist="exp(x) is always positive and grows fast, so larger scores dominate.",
                    is_first_principle=True,
                    grounding_guess="in_library",
                ),
                ConceptOut(
                    key="normalization",
                    title="Normalization to sum 1",
                    gist="Dividing by the total turns positive scores into weights over the keys.",
                    grounding_guess="in_library",
                ),
                ConceptOut(
                    key="max-subtraction",
                    title="Max subtraction",
                    gist="Subtracting the row maximum before exp avoids overflow without "
                    "changing the result.",
                    grounding_guess="in_library",
                ),
            ],
            links=[
                LinkOut(source="center", target="exponential", label="applies", kind="uses"),
                LinkOut(source="center", target="normalization", label="ends with", kind="part_of"),
                LinkOut(
                    source="max-subtraction",
                    target="exponential",
                    label="prevents overflow in",
                    kind="enables",
                ),
            ],
        )
    ]
)

SYSTEM = f"""\
You are building layers of a concept map of a research paper. Each item you get is one concept \
already on the map; for each item, build the layer one level below it: the sub-concepts that \
explain how it works or what it is made of, joined by labeled links.

Reply with a single JSON object and nothing else. It must match this JSON Schema:
```json
{SCHEMA_TEXT}
```

Constraints:
- layers: exactly one per item, in the order given. center_key echoes the item's key exactly.
- Each layer has between the requested minimum and maximum number of sub-concepts, one level \
lower than the item: its parts, steps, inputs or mechanisms, not restatements of it.
- Read each item in the context of its path: the path is the chain of concepts from the paper's \
core contribution down to the item, and fixes what the item means here ("Softmax" under \
attention means turning scores into weights over the keys).
- Never use the item itself or any concept on its path as a sub-concept.
- key: short kebab-case, unique within its layer. Never use "center" as a concept key.
- gist: one sentence of at most 25 words, paraphrased in your own words, never copied.
- is_first_principle: true for basic math, CS or ML primitives a motivated undergraduate accepts \
as a starting point (dot product, exponential function, probability distribution, matrix \
multiplication). These still appear as sub-concepts; the flag only stops further decomposition.
- anchors: leave empty unless the passages give anchor ids; never invent ids.
- Never state a number that is not in the passages or the summary.
- grounding_guess: "in_code" if the paper's code repository would implement it, "in_library" if \
it would come from a standard library, "concept_only" for pure ideas, "unknown" if unsure.
- links: directed, among the layer's sub-concepts and from "center" (the item). source and target \
are sub-concept keys or "center". The label is a short verb phrase so that \
"source - label -> target" reads as a sentence. Every sub-concept is linked at least once; \
include links between sub-concepts, not only from the center.

Example of a valid reply for one item "softmax" (abbreviated to 3 sub-concepts):
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _item_block(n: int, item: dict[str, Any]) -> str:
    lines = [
        f"Item {n}",
        f"key: {item['key']}",
        f"title: {item['title']}",
        f"gist: {item['gist']}",
        f"path: {' > '.join(item['path'])}",
    ]
    passages = [_clip(p, PASSAGE_CHARS) for p in item.get("anchor_texts", [])[:PASSAGES] if p]
    if passages:
        lines.append("passages from the paper:")
        lines += [f"- {p}" for p in passages]
    return "\n".join(lines)


def render(inputs: dict[str, Any]) -> list[Message]:
    items: list[dict[str, Any]] = inputs["items"]
    paper = "\n\n".join(
        [
            f"Paper: {inputs['paper_title']}\n"
            f"Summary: {_clip(inputs['paper_summary'], SUMMARY_CHARS)}",
            *(_item_block(n, item) for n, item in enumerate(items, 1)),
        ]
    )
    keys = ", ".join(json.dumps(item["key"]) for item in items)
    user = (
        f"Build one layer for each of these {len(items)} items, with "
        f"{inputs['min_children']}-{inputs['max_children']} sub-concepts each.\n\n"
        f"{untrusted('paper', defang(paper))}\n\n"
        f"Reply with {len(items)} layers whose center_key values are, in order: {keys}. "
        "Return the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="skeleton_batch",
    version=1,
    render=render,
    schema=SkeletonBatchOut,
    temperature=0.2,
    reasoning="low",
)
