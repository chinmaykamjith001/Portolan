"""`master_layer` (spec §10.5.1, Appendix D.1): the top layer of the Chart in one `strong` call.

Inputs are built by `build_inputs(doc)`:
    {"title": str, "abstract": str,
     "anchors": [{"id": str, "label": str, "kind": AnchorKind, "text": str}, ...]}  # doc order
"""

from __future__ import annotations

import json
from typing import Any

from portolan.llm.prompts.base import UNTRUSTED_NOTICE, PromptTemplate, untrusted
from portolan.llm.prompts.chart_schemas import CenterOut, ConceptOut, LinkOut, MasterLayerOut
from portolan.llm.types import Message
from portolan.models.paper import Anchor, PaperDoc

PARAGRAPH_CHARS = 1_200  # one paragraph (or caption) never takes more than this

SCHEMA_TEXT = json.dumps(MasterLayerOut.model_json_schema(), indent=2)

# Abbreviated: a real reply has 10-20 concepts.
EXAMPLE = MasterLayerOut(
    center=CenterOut(
        title="Transformer",
        gist="A sequence-to-sequence model built only from attention, with no recurrence.",
        anchors=["sec:3"],
    ),
    concepts=[
        ConceptOut(
            key="self-attention",
            title="Self-attention",
            gist="Each position attends to every position in the same sequence.",
            anchors=["sec:3.2", "eq:1"],
            grounding_guess="in_code",
        ),
        ConceptOut(
            key="positional-encoding",
            title="Positional encoding",
            gist="Sinusoids added to embeddings so the model can tell token positions apart.",
            anchors=["sec:3.5"],
            grounding_guess="in_code",
        ),
        ConceptOut(
            key="bleu",
            title="BLEU",
            gist="An n-gram overlap score used to compare translations with references.",
            anchors=["sec:6.1"],
            grounding_guess="concept_only",
        ),
    ],
    links=[
        LinkOut(source="center", target="self-attention", label="is built on", kind="uses"),
        LinkOut(
            source="self-attention",
            target="positional-encoding",
            label="is order-blind, so needs",
            kind="causes",
        ),
        LinkOut(source="center", target="bleu", label="is evaluated with", kind="other"),
    ],
)

SYSTEM = f"""\
You are building one layer of a concept map of a research paper: the master layer, the map a \
reader sees first. Its center is the paper's core contribution; around it are the 10-20 ideas a \
reader must understand to understand the paper, joined by labeled links.

Reply with a single JSON object and nothing else. It must match this JSON Schema:
```json
{SCHEMA_TEXT}
```

Constraints:
- center: the paper's core contribution (a model, method, finding or system), not the paper title.
- concepts: 10-20 of them. Concepts are IDEAS (mechanisms, problems, techniques, components, \
results as concepts), never section names. "Training setup", "Results" and "Related work" are \
wrong; "Label smoothing", "Learning-rate warmup" and "BLEU" are right.
- key: short kebab-case, unique in the reply. Never use "center" as a concept key.
- gist: one sentence of at most 25 words, paraphrased in your own words, never copied.
- anchors: only ids from the anchor list in the paper block (the bracketed ids). Cite the \
passages that introduce or define the concept; an empty list is fine when nothing fits.
- Never state a number that is not in the paper.
- is_first_principle: true only for basic math, CS or ML primitives a motivated undergraduate \
accepts as a starting point (dot product, matrix multiplication, probability distribution).
- grounding_guess: "in_code" if a code repository implementing the paper would contain it, \
"in_library" if it would come from a standard library (an optimizer, a tokenizer), \
"concept_only" for pure ideas, motivations or results, "unknown" if unsure.
- links: directed; source and target are concept keys or "center". The label is a short verb \
phrase so that "source - label -> target" reads as a sentence ("replaces", "is computed by", \
"is order-blind, so needs"). Aim for 1-2 links per concept and include cross-links between \
concepts, not only links from the center. kind is one of the schema's values; use "other" when \
none fits.

Example of a valid reply (abbreviated to 3 concepts; yours has 10-20):
```json
{EXAMPLE.model_dump_json(indent=2)}
```

{UNTRUSTED_NOTICE}"""


def defang(text: str) -> str:
    """Stop external text from closing the untrusted block early."""
    return text.replace("</untrusted", "<\\/untrusted")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cost(entry: dict[str, Any]) -> int:
    return len(entry["id"]) + len(entry["label"]) + len(entry["text"])


def _entry(anchor: Anchor, limit: int | None = None) -> dict[str, Any]:
    text = anchor.text if limit is None else _clip(anchor.text, limit)
    return {"id": anchor.id, "label": anchor.label, "kind": anchor.kind, "text": text}


def build_inputs(doc: PaperDoc, max_chars: int = 180_000) -> dict[str, Any]:
    """Pick the anchors the master-layer call sees, within a character budget.

    Always kept: every section title and every main-body equation. The rest of the budget is
    filled in document order with main-body paragraphs and captions (each clipped to 1,200
    chars), then appendix equations, paragraphs and captions. So the appendix is what goes
    first when the paper is over budget. Title and abstract count towards the budget.
    """
    anchors = sorted(doc.anchors, key=lambda a: a.order)
    chosen: dict[str, dict[str, Any]] = {}
    for a in anchors:
        if a.kind == "section" or (a.kind == "equation" and not a.is_appendix):
            chosen[a.id] = _entry(a)
    used = len(doc.title) + len(doc.abstract) + sum(_cost(e) for e in chosen.values())

    fill = [a for a in anchors if a.id not in chosen and not a.is_appendix]
    fill += [a for a in anchors if a.id not in chosen and a.is_appendix]
    for a in fill:
        entry = _entry(a, None if a.kind == "equation" else PARAGRAPH_CHARS)
        if not entry["text"]:
            continue
        cost = _cost(entry)
        if used + cost <= max_chars:  # skip what doesn't fit; a later, shorter one may
            chosen[a.id] = entry
            used += cost

    ordered = [chosen[a.id] for a in anchors if a.id in chosen]
    return {"title": doc.title, "abstract": doc.abstract, "anchors": ordered}


def _anchor_line(entry: dict[str, Any]) -> str:
    line = f"[{entry['id']}] {entry['label']}: {entry['text']}"
    return f"\n## {line}" if entry["kind"] == "section" else line


def render(inputs: dict[str, Any]) -> list[Message]:
    paper = "\n".join(
        [
            f"Title: {inputs['title']}",
            "",
            f"Abstract: {inputs['abstract']}",
            "",
            "Anchors, in document order (format: [id] label: text):",
            *(_anchor_line(e) for e in inputs["anchors"]),
        ]
    )
    user = (
        "Build the master layer for this paper.\n\n"
        f"{untrusted('paper', defang(paper))}\n\n"
        "Return the JSON object only."
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user)]


TEMPLATE = PromptTemplate(
    id="master_layer",
    version=1,
    render=render,
    schema=MasterLayerOut,
    temperature=0.2,
    reasoning="medium",
)
