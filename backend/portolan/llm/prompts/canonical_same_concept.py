"""`canonical_same_concept` (spec §10.5.3): are two close candidates one concept or two?

Inputs: {"a_title": str, "a_gist": str, "b_title": str, "b_gist": str}
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.chart_schemas import SameConceptOut
from portolan.llm.prompts.master_layer import defang
from portolan.llm.types import Message

SCHEMA_TEXT = json.dumps(SameConceptOut.model_json_schema(), indent=2)

EXAMPLE = SameConceptOut(
    same=False,
    reason="RMSNorm only rescales by the root mean square; LayerNorm also subtracts the mean.",
)

SYSTEM = f"""\
You are deduplicating the concepts of a concept map of a research paper. You get two candidate \
concepts, A and B, each with a title and a one-sentence gist. Decide whether they are the same \
concept, so the map should show one node for both.

Reply with a single JSON object and nothing else. It must match this JSON Schema:
```json
{SCHEMA_TEXT}
```

Constraints:
- same: true only when A and B name the same idea, differing at most in wording, abbreviation \
or spelling ("Multi-head attention" and "MHA"; "Positional encoding" and "Position encodings").
- Be conservative. Related, similar, variant, generalised or specialised concepts are different: \
"LayerNorm" and "RMSNorm" are different; "Attention" and "Self-attention" are different; \
"Dropout" and "Attention dropout" are different. When unsure, answer false.
- reason: one short sentence naming what makes them the same or different.

Example. A: "Layer normalization" (normalizes activations by their mean and variance). \
B: "RMSNorm" (rescales activations by their root mean square). Reply:
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def render(inputs: dict[str, Any]) -> list[Message]:
    pair = (
        f"A: {inputs['a_title']}\n"
        f"A gist: {inputs['a_gist']}\n\n"
        f"B: {inputs['b_title']}\n"
        f"B gist: {inputs['b_gist']}"
    )
    user = (
        "Are A and B the same concept?\n\n"
        f"{untrusted('paper', defang(pair))}\n\n"
        "Return the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="canonical_same_concept",
    version=1,
    render=render,
    schema=SameConceptOut,
    temperature=0.2,
    reasoning="minimal",
)
