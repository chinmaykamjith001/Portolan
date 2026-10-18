"""Demo findings for `demo:toyformer` (no network, no LLM).

They agree with the seeded layer-normalization grounding: the paper normalizes after the residual
add, the code before the sublayer. Issue counts are set through the same path the engine uses.
"""

from __future__ import annotations

from portolan.models.code import CodeSpan
from portolan.models.finding import Finding
from portolan.store.chart import ChartStore
from portolan.store.db import Database
from portolan.store.findings import FindingStore
from portolan.verify.issues import recount
from portolan.verify.reverse import finding_id

THRESHOLD = 0.7  # the default `mismatch_confidence_threshold`


def _span(path: str, line: int) -> CodeSpan:
    from portolan.dev.seed import COMMIT, REPO_ID  # seed.py imports this module at load time

    return CodeSpan(repo_id=REPO_ID, commit_sha=COMMIT, path=path, start_line=line, end_line=line)


def demo_findings() -> list[Finding]:
    layers, attn = "toyformer/layers.py", "toyformer/attention.py"
    return [
        Finding(
            id=finding_id("sec:2", "norm"),
            claim_text="Each sublayer's output is added to its input and then layer-normalized.",
            claim_type="architecture",
            paper_anchor="sec:2",
            code_spans=[_span(layers, 28)],
            verdict="mismatch",
            severity="high",
            confidence=0.86,
            method="llm_judge",
            evidence="Paper (§2): the sum of input and sublayer output is normalized "
            "(post-norm). Code: toyformer/layers.py:28 normalizes the input before the "
            "sublayer and adds it back unnormalized (pre-norm).",
            concept_ids=["layer-normalization"],
        ),
        Finding(
            id=finding_id("eq:1", "scale"),
            claim_text="Attention scores are divided by the square root of the key size.",
            claim_type="algorithm_step",
            paper_anchor="eq:1",
            code_spans=[_span(attn, 14)],
            verdict="matches",
            severity="low",
            confidence=0.92,
            method="llm_judge",
            evidence="Paper (Eq. 1): QKᵀ is divided by √dₖ. Code: toyformer/attention.py:14 "
            "divides the scores by math.sqrt(d_k).",
            concept_ids=["scaled-dot-product"],
        ),
        Finding(
            id=finding_id("reverse", attn, 16, "masking"),
            claim_text="Padding scores are set to -1e9 before the softmax",
            claim_type="other",
            paper_anchor="",
            code_spans=[_span(attn, 16)],
            verdict="undocumented_in_paper",
            severity="low",
            confidence=0.75,
            method="llm_judge",
            evidence="toyformer/attention.py:16 fills masked scores with -1e9 so the softmax "
            "gives them no weight; the paper never says how masking is done.",
            concept_ids=["scaled-dot-product"],
        ),
        Finding(
            id=finding_id("eq:4", "warmup"),
            claim_text="The learning rate rises linearly for the first warmup steps.",
            claim_type="hyperparameter",
            paper_anchor="eq:4",
            code_spans=[],
            verdict="uncertain",
            severity="low",
            confidence=0.5,
            method="llm_judge",
            evidence="Paper (Eq. 4): linear warmup then inverse-square-root decay. Code: the "
            "schedule is only partly visible, so the two could not be compared.",
            concept_ids=["warmup"],
        ),
    ]


def seed_findings(db: Database, paper_id: str) -> None:
    """Write the demo paper's findings and set the issue counts on its concepts."""
    from portolan.dev.seed import COMMIT

    store = FindingStore(db)
    store.delete_paper(paper_id)
    for f in demo_findings():
        store.save(paper_id, f)
    store.set_run(paper_id, "done", repo_commit=COMMIT)
    recount(ChartStore(db), paper_id, store.all(paper_id), THRESHOLD)
