"""Demo FAQ for `demo:toyformer` (no network, no LLM): five source kinds, one unsupported ask.

Every concept id and anchor id below exists in `portolan.dev.seed`; the test checks it. Source
URLs point at the demo's own made-up forum and repository, so they illustrate the labels only.
"""

from __future__ import annotations

from portolan.models.finding import FAQItem
from portolan.store.db import Database
from portolan.store.faq import FaqStore

FORUM = "https://openreview.net/forum?id=demo-toyformer"
ISSUES = "https://github.com/example/toyformer/issues"
UNSUPPORTED = "The paper and code don't answer this."

_REVIEW = {"source_kind": "reviewer_discussion", "source_label": "From reviewer discussion"}
_ASKED = {"source_kind": "user_asked", "source_label": "Your question"}

ITEMS: list[dict[str, object]] = [
    {
        "id": "faq-demo-01",
        "question": "Why are the dot products divided by the square root of the key size?",
        "answer_markdown": (
            "Each score is a sum of $d_k$ products, so its spread grows with $d_k$. Large scores "
            "push softmax toward a one-hot output where almost no gradient flows. Dividing by "
            "$\\sqrt{d_k}$ keeps the variance near one, so training stays stable (Eq. 1)."
        ),
        "source_url": FORUM,
        "concept_ids": ["sqrt-dk-scaling", "scaled-dot-product"],
        "anchors_cited": ["eq:1"],
        **_REVIEW,
    },
    {
        "id": "faq-demo-02",
        "question": "Why use fixed sinusoids for positions instead of learned embeddings?",
        "answer_markdown": (
            "The authors found both work about equally well. Sinusoids add no parameters, and "
            "because a shift by a fixed offset is a linear function of the encoding, they may let "
            "the model attend by relative position (Eq. 3)."
        ),
        "source_url": FORUM,
        "concept_ids": ["sinusoids", "relative-offsets"],
        "anchors_cited": ["eq:3", "sec:2.2"],
        **_REVIEW,
    },
    {
        "id": "faq-demo-03",
        "question": "Why does training warm the learning rate up first?",
        "answer_markdown": (
            "Early in training the gradients are noisy and the weights random, so a full-size "
            "step can send the loss far off. The schedule rises linearly for the first steps and "
            "then decays with the inverse square root of the step (Eq. 4)."
        ),
        "source_url": f"{ISSUES}/12",
        "source_label": "From GitHub issue #12",
        "source_kind": "github_issue",
        "concept_ids": ["warmup"],
        "anchors_cited": ["eq:4", "sec:3"],
    },
    {
        "id": "faq-demo-04",
        "question": "Is label smoothing applied when evaluating the model?",
        "answer_markdown": (
            "No. Smoothing softens the targets only in the training loss; evaluation measures "
            "accuracy and perplexity against the original labels (§3)."
        ),
        "source_url": f"{ISSUES}/31",
        "source_label": "From GitHub issue #31",
        "source_kind": "github_issue",
        "concept_ids": ["label-smoothing"],
        "anchors_cited": ["sec:3"],
    },
    {
        "id": "faq-demo-05",
        "question": "Does the code use layer normalization with the epsilon given in the paper?",
        "answer_markdown": (
            "Not exactly. The paper leaves epsilon unstated, while the code relies on the "
            "library default, which differs from the value the text implies."
        ),
        "source_label": "Finding",
        "source_kind": "finding",
        "concept_ids": ["layer-normalization"],
        "anchors_cited": ["sec:2"],
    },
    {
        "id": "faq-demo-06",
        "question": "Why does attention need positional encoding at all?",
        "answer_markdown": (
            "Attention mixes tokens by similarity and ignores where they sit, so shuffling the "
            "input would not change the output. Adding a position signal to each token lets the "
            "model tell order apart (§2.2)."
        ),
        "source_label": "Anticipated",
        "source_kind": "anticipated",
        "concept_ids": ["positional-encoding"],
        "anchors_cited": ["sec:2.2"],
    },
    {
        "id": "faq-demo-07",
        "question": "Why softmax and not just pick the highest score?",
        "answer_markdown": (
            "Picking the maximum is all-or-nothing and has no useful gradient. Softmax keeps "
            "every weight positive and summing to one, so a token can blend several others "
            "(Eq. 2)."
        ),
        "concept_ids": ["softmax"],
        "anchors_cited": ["eq:2"],
        **_ASKED,
    },
    {
        "id": "faq-demo-08",
        "question": "Does Toyformer support mixed-precision training?",
        "answer_markdown": UNSUPPORTED,
        "concept_ids": [],
        "anchors_cited": [],
        "supported": False,
        **_ASKED,
    },
]


def seed_faq(db: Database, paper_id: str) -> None:
    """Write the demo paper's FAQ (idempotent: same ids replace)."""
    store = FaqStore(db)
    for raw in ITEMS:
        store.save(paper_id, FAQItem.model_validate(raw))
