"""`route_section` (spec §10.6.2): writes the stops of one section of the Route.

Inputs:
    {"paper_title": str,
     "section": {"label": str, "title": str},      # the top-level section (or the parent stop)
     "next_section": {"label": str, "title": str} | None,  # None at the end of the route
     "stops": [{"key": "s1", "label": str, "kind": "section" | "passage",
                "title": str, "text": str}, ...],   # in the paper's order
     "concepts": [{"id": str, "title": str, "gist": str}, ...]}   # candidates, may be empty
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.master_layer import defang
from portolan.llm.prompts.route_schemas import RouteSectionOut, RouteStopOut
from portolan.llm.types import Message

STOP_CHARS = 1800
MAX_STOPS = 14
MAX_CONCEPTS = 40
SCHEMA_TEXT = json.dumps(RouteSectionOut.model_json_schema(), indent=2)

EXAMPLE = RouteSectionOut(
    stops=[
        RouteStopOut(
            key="s1",
            title="Why recurrence is the bottleneck",
            role="setting",
            summary="Recurrent models process tokens one at a time, so training cannot be "
            "parallelised across a sequence and long sequences become expensive.",
            concept_id=None,
            transition_to_next="so the authors replace recurrence with attention,",
        ),
        RouteStopOut(
            key="s2",
            title="Attention as the only mixing step",
            role="definition",
            summary="The model lets every position look at every other position directly, "
            "using attention in place of recurrence or convolution.",
            concept_id="self-attention",
            transition_to_next=None,
        ),
    ]
)

SYSTEM = f"""\
You are laying out one section of a research paper as stops on a road that a reader walks in the \
paper's own order. For each stop you are given a passage; describe it.

For every stop, in the same order and with the same key:
- title: at most 8 words. Say what the paper does at this point ("Why recurrence is the \
bottleneck"), not just the heading.
- role: one of first_mention, definition, setting, result, argument, other.
- summary: a PARAPHRASE of what the paper says here, at most 40 words. Do not quote more than a \
short phrase. Do not add claims, numbers or results that are not in the passage.
- concept_id: the id of the candidate concept this stop is mainly about, copied exactly from the \
list, or null. Never invent an id.
- transition_to_next: the connective that leads to the next stop, written to read naturally on a \
road sign, lowercase, at most 12 words, ending with a comma ("so the authors drop recurrence,"). \
For the last stop of the section, lead into the next section if one is given; otherwise null.

Rules:
- One output stop per input stop; keep the keys.
- Plain words; no Markdown.

Output JSON only, matching this schema exactly:
```json
{SCHEMA_TEXT}
```

Example of the format (shorter than yours):
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render(inputs: dict[str, Any]) -> list[Message]:
    section = inputs["section"]
    nxt = inputs.get("next_section")
    stops = inputs["stops"][:MAX_STOPS]
    passages = "\n\n".join(
        f"[{s['key']}] {s['label']} — {s['title']}\n{_clip(str(s['text']), STOP_CHARS)}"
        for s in stops
    )
    candidates: list[dict[str, Any]] = inputs.get("concepts") or []
    concepts = "\n".join(
        f"{c['id']}: {c['title']} — {_clip(str(c.get('gist', '')), 120)}"
        for c in candidates[:MAX_CONCEPTS]
    )
    next_line = (
        f"The next section is {nxt['label']} {nxt['title']}."
        if nxt
        else "This is the end of the route: the last stop's transition_to_next must be null."
    )
    paper = f"Paper: {inputs['paper_title']}\n\nPassages:\n{passages}"
    user = (
        f"Section: {section['label']} {section['title']}\n{next_line}\n\n"
        f"Candidate concepts (id: title — gist):\n{concepts or '(none)'}\n\n"
        f"{untrusted('paper', defang(paper))}\n\n"
        "Return the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="route_section",
    version=1,
    render=render,
    schema=RouteSectionOut,
    temperature=0.3,
    reasoning="low",
)
