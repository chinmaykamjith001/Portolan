"""`claims_extract` (spec §10.9.1): checkable claims from the method, training and evaluation text.

Inputs:
    {"paper_title": str,
     "passages": [{"id": str, "label": str, "text": str}, ...],   # a batch of consecutive passages
     "symbols": {str: str}}                                         # the paper's notation table
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.master_layer import defang
from portolan.llm.prompts.verify_schemas import ClaimOut, ClaimsOut
from portolan.llm.types import Message

PASSAGE_CHARS = 1400
SCHEMA_TEXT = json.dumps(ClaimsOut.model_json_schema(), indent=2)

EXAMPLE = ClaimsOut(
    claims=[
        ClaimOut(
            anchor="para:5.3:1",
            claim_type="hyperparameter",
            subject="dropout rate",
            value="0.1",
            text="Dropout with rate 0.1 is applied to the output of each sub-layer.",
        ),
        ClaimOut(
            anchor="para:3.1:1",
            claim_type="architecture",
            subject="number of encoder layers",
            value="6",
            text="The encoder is a stack of 6 identical layers.",
        ),
    ]
)

SYSTEM = f"""\
You read passages from a research paper and list the CHECKABLE claims about how the method is \
built and trained, so someone can compare each claim with the paper's code.

Include claims about: hyperparameters (learning rate, dropout, batch size, number of layers or \
heads, dimensions, warmup steps, label smoothing, optimizer settings), architecture facts (what \
is stacked, in what order, where normalization or residuals sit), algorithm steps, the objective \
or loss, data processing, and the evaluation protocol.

For every claim give:
- anchor: the id of the passage it comes from, exactly as given.
- claim_type, subject (what is being claimed about, in plain words), value (as the paper writes \
it, or null when the claim is not a number) and text (one short paraphrase, at most 30 words).

Rules:
- One claim per fact. Skip results, motivation and related work.
- Only claims the passages state outright; never infer a value that isn't written.
- Paraphrase; do not copy sentences.

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
    passages = "\n\n".join(
        f"[{p['id']}] {p.get('label', '')}\n{str(p['text'])[:PASSAGE_CHARS]}"
        for p in inputs["passages"]
    )
    symbols: dict[str, str] = inputs.get("symbols") or {}
    notation = "\n".join(f"{k}: {v}" for k, v in list(symbols.items())[:30]) or "(none extracted)"
    user = (
        f"Paper: {inputs['paper_title']}\n\nNotation:\n{untrusted('notation', defang(notation))}"
        f"\n\nPassages:\n{untrusted('paper', defang(passages))}\n\nReturn the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="claims_extract",
    version=1,
    render=render,
    schema=ClaimsOut,
    temperature=0.1,
    reasoning="medium",
)
