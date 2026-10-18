"""Graph templates: data shapes, the optional-graph rules, and the number guard."""

from __future__ import annotations

from typing import Any

import pytest

from portolan.detail import graph as graph_module
from portolan.detail.explain import DetailError
from portolan.detail.graph import _Invalid, normalise  # pyright: ignore[reportPrivateUsage]
from portolan.llm.types import LLMError, ProviderResponse
from portolan.models.detail import GraphSpec
from portolan.sandbox import run_program
from portolan.store.db import Database
from tests.conftest import ScriptedProvider
from tests.detail_helpers import (
    CONCEPT_ID,
    FakeSandbox,
    failed,
    gateway,
    good,
    paper,
    reply,
    store,
)

PROGRAM = (
    "import math\nimport json\nxs = [i / 2 for i in range(-6, 7)]\n"
    "ys = [1 / (1 + math.exp(-x)) for x in xs]\n"
    'print(json.dumps({"x": xs, "series": [{"name": "w", "y": ys}]}))'
)
LINE = {"x": [0, 1, 2], "series": [{"name": "w", "y": [0.5, 0.731, 0.881]}], "y_label": "weight"}


def llm(**over: object) -> ProviderResponse:
    return reply(
        **{
            "template": "line",
            "caption": "Sigmoid of the score",
            "program": PROGRAM,
            "note": "The curve flattens.",
            **over,
        }
    )


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    box = FakeSandbox([])
    monkeypatch.setattr(graph_module, "run_program", box)
    return box


async def build(db: Database, provider: ScriptedProvider) -> GraphSpec | None:
    return await graph_module.make_graph(paper(), gateway(db, provider), store(db), CONCEPT_ID)


async def test_a_line_graph_carries_the_executed_data(db: Database, sandbox: FakeSandbox) -> None:
    sandbox.results = [good(LINE)]
    provider = ScriptedProvider([llm()])
    spec = await build(db, provider)
    assert spec is not None
    assert (spec.concept_id, spec.template, spec.caption) == (
        CONCEPT_ID,
        "line",
        "Sigmoid of the score",
    )
    assert spec.data == {
        "x": [0.0, 1.0, 2.0],
        "series": [{"name": "w", "y": [0.5, 0.731, 0.881]}],
        "x_label": "",
        "y_label": "weight",
    }
    assert spec.note == "The curve flattens."
    assert len(provider.calls) == 1


async def test_none_means_no_graph_and_runs_nothing(db: Database, sandbox: FakeSandbox) -> None:
    provider = ScriptedProvider([reply(template="none")])
    assert await build(db, provider) is None
    assert sandbox.sources == [] and len(provider.calls) == 1


async def test_numbers_typed_by_the_llm_never_reach_the_graph(
    db: Database, sandbox: FakeSandbox
) -> None:
    sandbox.results = [good(LINE)]
    provider = ScriptedProvider(
        [llm(caption="Weight reaches 0.95 at x=9", note="It rises to 0.881 by x=2.")]
    )
    spec = await build(db, provider)
    assert spec is not None
    assert spec.caption == "Softmax"  # fell back to the concept title: 0.95 and 9 aren't in the run
    assert spec.note == "It rises to 0.881 by x=2."  # 0.881 and 2 are in the data


async def test_a_failing_program_is_retried_once_then_the_graph_is_dropped(
    db: Database, sandbox: FakeSandbox
) -> None:
    sandbox.results = [failed("Timed out after 5 seconds."), failed("Timed out after 5 seconds.")]
    provider = ScriptedProvider([llm(), llm(program=PROGRAM + "\n# again")])
    assert await build(db, provider) is None  # a graph is optional: no exception
    assert len(provider.calls) == 2
    assert "Timed out" in provider.calls[1][1][-1].content


async def test_invalid_data_triggers_the_retry(db: Database, sandbox: FakeSandbox) -> None:
    sandbox.results = [good({"x": [0, 1], "series": []}), good(LINE)]
    provider = ScriptedProvider([llm(), llm()])
    spec = await build(db, provider)
    assert spec is not None and len(provider.calls) == 2
    assert '"series" must be a list of 1 to 6 items' in provider.calls[1][1][-1].content


async def test_a_provider_failure_is_not_cached_as_no_graph(db: Database) -> None:
    class Boom(LLMError):
        pass

    provider = ScriptedProvider([Boom("provider is down")])
    with pytest.raises(LLMError):
        await build(db, provider)  # the job fails and can be retried; `None` would stick


async def test_unknown_concept(db: Database) -> None:
    with pytest.raises(DetailError):
        await graph_module.make_graph(paper(), gateway(db, ScriptedProvider([])), store(db), "nope")


# -- shapes ---------------------------------------------------------------------------------


def test_line_requires_ascending_numbers_and_equal_lengths() -> None:
    ok_line = {"x": [0, 1], "series": [{"name": "a", "y": [1, 2]}]}
    assert normalise("line", ok_line)["x"] == [0.0, 1.0]
    for bad in (
        {"x": [1, 0], "series": [{"name": "a", "y": [1, 2]}]},
        {"x": ["a", "b"], "series": [{"name": "a", "y": [1, 2]}]},
        {"x": [0, 1], "series": [{"name": "a", "y": [1]}]},
        {"x": [0], "series": [{"name": "a", "y": [1]}]},
        {"x": [0, 1], "series": [{"name": "a", "y": [1, float("inf")]}]},
        {"x": [0, 1], "series": [{"name": "a", "y": [1, True]}]},
    ):
        with pytest.raises(_Invalid):
            normalise("line", bad)


def test_bar_takes_category_labels_and_caps_series() -> None:
    data = normalise("bar", {"x": ["a", 2, 3.5], "series": [{"y": [1, 2, 3]}], "x_label": "k"})
    assert data == {
        "x": ["a", "2", "3.5"],
        "series": [{"name": "Series 1", "y": [1.0, 2.0, 3.0]}],
        "x_label": "k",
        "y_label": "",
    }
    seven = {"x": ["a"], "series": [{"name": str(i), "y": [1]} for i in range(7)]}
    with pytest.raises(_Invalid):
        normalise("bar", seven)


def test_heatmap_checks_the_matrix_against_the_labels() -> None:
    good_map = {"rows": ["q1", "q2"], "cols": ["k1", "k2", "k3"], "values": [[1, 2, 3], [4, 5, 6]]}
    assert normalise("heatmap", good_map)["values"] == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    with pytest.raises(_Invalid):
        normalise("heatmap", {**good_map, "values": [[1, 2, 3]]})
    with pytest.raises(_Invalid):
        normalise("heatmap", {**good_map, "values": [[1, 2], [3, 4]]})
    with pytest.raises(_Invalid):
        normalise("heatmap", {"rows": ["r"] * 25, "cols": ["c"], "values": [[1]] * 25})


def test_before_after_defaults_its_names() -> None:
    data = normalise("before_after", {"labels": ["a", "b"], "before": [1, 2], "after": [2, 1]})
    assert (data["before_name"], data["after_name"]) == ("Before", "After")
    with pytest.raises(_Invalid):
        normalise("before_after", {"labels": ["a", "b"], "before": [1], "after": [2, 1]})


def test_distribution_bins_raw_values_and_accepts_curves() -> None:
    hist = normalise("distribution", {"values": [0, 1, 1, 2, 2, 2, 3, 3, 4, 10], "bins": 5})
    assert hist["style"] == "bars" and len(hist["x"]) == 5  # type: ignore[arg-type]
    assert sum(hist["y"]) == 10 and hist["bin_width"] == 2.0  # type: ignore[arg-type]
    curve = normalise("distribution", {"x": [0, 1, 2], "y": [0.2, 0.5, 0.3]})
    assert (curve["style"], curve["bin_width"]) == ("curve", None)
    bars = normalise("distribution", {"x": [0, 1, 2], "y": [1, 2, 1], "style": "bars"})
    assert bars["bin_width"] == 1.0
    with pytest.raises(_Invalid):
        normalise("distribution", {"values": [3, 3, 3]})  # nothing to show
    with pytest.raises(_Invalid):
        normalise("distribution", {"x": [0, 1], "y": [1]})


def test_rejects_non_objects_and_unknown_templates() -> None:
    with pytest.raises(_Invalid):
        normalise("bar", [1, 2, 3])
    with pytest.raises(_Invalid):
        normalise("pie", {})


def test_labels_are_clipped() -> None:
    data: dict[str, Any] = normalise(
        "bar", {"x": ["z" * 100], "series": [{"name": "n" * 99, "y": [1]}]}
    )
    assert len(data["x"][0]) == 40 and len(data["series"][0]["name"]) == 40


def test_docstring_documents_every_template() -> None:
    for template in ("bar", "line", "heatmap", "before_after", "distribution"):
        assert f"\n{template}\n" in graph_module.__doc__  # type: ignore[operator]


async def test_real_sandbox_end_to_end(db: Database) -> None:
    probe = await run_program("print(1)")
    assert probe.ok, f"the sandbox itself fails on this machine: {probe.error}"
    spec = await build(db, ScriptedProvider([llm()]))
    assert spec is not None and spec.template == "line"
    assert len(spec.data["x"]) == 13 and spec.data["series"][0]["y"][6] == 0.5  # type: ignore[index]
