"""Worked examples: the table comes from the sandbox, LLM-typed numbers never reach the UI."""

from __future__ import annotations

import pytest

from portolan.detail import example as example_module
from portolan.detail.example import make_example, table_from_output
from portolan.detail.explain import DetailError
from portolan.detail.grounded_text import NumberGuard, numbers_in
from portolan.llm.types import ProviderResponse
from portolan.models.detail import Example
from portolan.sandbox import run_program
from portolan.store.db import Database
from tests.conftest import ScriptedProvider
from tests.detail_helpers import (
    CONCEPT_ID,
    PAPER_ID,
    FakeSandbox,
    failed,
    gateway,
    good,
    paper,
    reply,
    store,
)

PROGRAM = (
    "import numpy as np\nimport json\nz = np.array([2.0, 1.0, 0.1])\n"
    "p = np.exp(z - z.max()); p /= p.sum()\n"
    'print(json.dumps({"columns": ["score", "weight"], '
    '"rows": [[f"{a:.1f}", f"{b:.3f}"] for a, b in zip(z, p)]}))'
)
TABLE = {
    "columns": ["score", "weight"],
    "rows": [["2.0", "0.659"], ["1.0", "0.242"], ["0.1", "0.099"]],
}


def llm(**over: object) -> ProviderResponse:
    return reply(
        **{
            "caption": "softmax([2.0, 1.0, 0.1])",
            "source_label": "paper_notation",
            "program": PROGRAM,
            "note": "The weights sum to 1.",
            **over,
        }
    )


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    box = FakeSandbox([])
    monkeypatch.setattr(example_module, "run_program", box)
    return box


async def build(
    db: Database,
    provider: ScriptedProvider,
    variant: int = 0,
    previous: list[Example] | None = None,
) -> Example:
    return await make_example(
        paper(), gateway(db, provider), store(db), CONCEPT_ID, variant, previous or []
    )


async def test_the_table_is_what_the_program_printed(db: Database, sandbox: FakeSandbox) -> None:
    sandbox.results = [good(TABLE)]
    provider = ScriptedProvider([llm()])
    ex = await build(db, provider)
    assert (ex.columns, ex.rows) == (TABLE["columns"], TABLE["rows"])
    assert ex.program == PROGRAM and sandbox.sources == [PROGRAM]
    assert (ex.concept_id, ex.variant, ex.source_label) == (CONCEPT_ID, 0, "paper_notation")
    assert ex.caption == "softmax([2.0, 1.0, 0.1])"
    assert ex.note == "The weights sum to 1."  # "1" appears in the program and the table
    # One P0 call to the default model, with the concept and its passages in the prompt.
    assert len(provider.calls) == 1
    user = provider.calls[0][1][-1].content
    assert "Concept: Softmax" in user and "[eq:1] Eq. 1" in user


async def test_numbers_typed_by_the_llm_never_reach_the_example(
    db: Database, sandbox: FakeSandbox
) -> None:
    sandbox.results = [good(TABLE)]
    provider = ScriptedProvider(
        [
            llm(
                caption="softmax over 17 scores",  # 17 was never computed
                note="The top weight is 0.99, a 40% jump.",
            )
        ]
    )
    ex = await build(db, provider)
    assert ex.note is None  # 0.99 and 40 are not in the run
    assert ex.caption == "Worked example: Softmax"
    assert "17" not in ex.caption and "0.99" not in (ex.note or "")
    assert "0.99" not in str(ex.model_dump())


async def test_numbers_that_the_run_produced_are_kept(db: Database, sandbox: FakeSandbox) -> None:
    sandbox.results = [good(TABLE)]
    provider = ScriptedProvider(
        [llm(note="The largest score, 2.0, gets about 0.66 of the weight; d_k and x2 are names.")]
    )
    ex = await build(db, provider)
    # 2.0 is in the table, 0.66 is 0.659 rounded; "d_k" and "x2" aren't numbers.
    assert ex.note is not None and "0.66" in ex.note


async def test_a_failing_program_is_retried_once_with_the_problem(
    db: Database, sandbox: FakeSandbox
) -> None:
    sandbox.results = [
        failed("The program raised NameError: name 'q' is not defined."),
        good(TABLE),
    ]
    provider = ScriptedProvider([llm(program="print(q)"), llm()])
    ex = await build(db, provider)
    assert ex.rows == TABLE["rows"] and len(provider.calls) == 2
    retry_prompt = provider.calls[1][1][-1].content
    assert "Your previous program failed." in retry_prompt
    assert "print(q)" in retry_prompt and "NameError" in retry_prompt
    assert sandbox.sources == ["print(q)", PROGRAM]


async def test_two_failures_raise_a_plain_error(db: Database, sandbox: FakeSandbox) -> None:
    sandbox.results = [failed("Timed out after 5 seconds."), failed("Timed out after 5 seconds.")]
    provider = ScriptedProvider([llm(), llm(program=PROGRAM + "\n# again")])
    with pytest.raises(DetailError, match="Couldn't build a worked example"):
        await build(db, provider)
    assert len(provider.calls) == 2  # never a third request


@pytest.mark.parametrize(
    ("output", "problem"),
    [
        ("just text", "Print one JSON object"),
        ({"columns": [], "rows": []}, "non-empty"),
        ({"columns": ["a", "b"], "rows": [["1"]]}, "one cell per column"),
        ({"columns": ["a"], "rows": [[float("nan")]]}, "NaN or infinite"),
        ({"columns": ["a"], "rows": [[[1, 2]]]}, "strings or numbers"),
    ],
)
async def test_an_unusable_table_counts_as_a_failed_attempt(
    db: Database, sandbox: FakeSandbox, output: object, problem: str
) -> None:
    sandbox.results = [good(output), good(TABLE)]
    provider = ScriptedProvider([llm(), llm()])
    ex = await build(db, provider)
    assert ex.rows == TABLE["rows"]
    assert problem in provider.calls[1][1][-1].content


async def test_empty_and_overlong_programs_never_reach_the_sandbox(
    db: Database, sandbox: FakeSandbox
) -> None:
    sandbox.results = [good(TABLE)]
    long_program = "\n".join(f"x{i} = {i}" for i in range(80))
    provider = ScriptedProvider([llm(program="  "), llm(program=long_program), llm()])
    with pytest.raises(DetailError):
        await build(db, provider)  # two attempts, both rejected before running anything
    assert sandbox.sources == []


async def test_a_later_variant_sees_the_earlier_examples(
    db: Database, sandbox: FakeSandbox
) -> None:
    sandbox.results = [good(TABLE), good(TABLE)]
    first = await build(db, ScriptedProvider([llm()]))
    provider = ScriptedProvider([llm(caption="softmax([1000.0, 1000.0])")])
    second = await build(db, provider, variant=1, previous=[first])
    assert second.variant == 1
    user = provider.calls[0][1][-1].content
    assert "DIFFERENT case" in user and "Earlier example 1: softmax([2.0, 1.0, 0.1])" in user


async def test_unknown_concept(db: Database, sandbox: FakeSandbox) -> None:
    with pytest.raises(DetailError, match="isn't on this paper's map"):
        await make_example(paper(), gateway(db, ScriptedProvider([])), store(db), "nope", 0, [])


def test_table_caps_and_stringifies() -> None:
    wide = {
        "columns": [f"c{i}" for i in range(12)],
        "rows": [[i, 0.5, True, None, "x" * 100] + [0] * 7 for i in range(20)],
    }
    columns, rows = table_from_output(wide)
    assert len(columns) == 8 and len(rows) == 12 and all(len(r) == 8 for r in rows)
    assert rows[1][:5] == ["1", "0.5", "True", "None", "x" * 40]


def test_number_parsing_ignores_identifiers() -> None:
    text = "d_k = 64, W1, x2, v1.5, 3rd, 1,000 items, 0.5 and 1e-3 or 2."
    assert [v for v, _ in numbers_in(text)] == [64.0, 1000.0, 0.5, 0.001, 2.0]
    assert [d for _, d in numbers_in("0.250 and 3")] == [3, 0]


def test_number_guard_rounding_is_one_way() -> None:
    guard = NumberGuard(["0.659 and 2.718"])
    assert guard.check("about 0.66") == "about 0.66"
    assert guard.check("about 0.7") == "about 0.7"
    assert guard.check("about 3") is None  # an integer must match exactly
    assert guard.check("-0.659") == "-0.659"  # sign is ignored, the magnitude is real
    assert guard.check("0.67") is None
    assert guard.check(None) is None and guard.check("no digits at all") == "no digits at all"


async def test_real_sandbox_end_to_end(db: Database) -> None:
    try:
        await run_program("print(1)")
    except NotImplementedError:
        pytest.skip("sandbox not implemented yet")
    provider = ScriptedProvider([llm()])
    ex = await build(db, provider)
    assert ex.rows == TABLE["rows"]  # computed by really running PROGRAM
    assert PAPER_ID.startswith("arxiv")
