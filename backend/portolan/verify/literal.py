"""literal_match (spec §10.9.3a): compare a paper value with a named value in the code. No LLM.

Precision first: a mismatch needs a value in the code that is named exactly like the claim's
subject and disagrees, with nothing else in the code agreeing. Exactly-named values that agree
and disagree at once are left to the judge (which default applies is not a literal question).
A merely related name (`attention_dropout` for "dropout rate") can confirm a match but never
produces a mismatch.
"""

from __future__ import annotations

from portolan.verify.localize import CodeContext
from portolan.verify.types import Claim, Decision, ValueItem
from portolan.verify.values import canon_words, name_match, subject_views, value_matches

CONF_MATCH = 0.95
CONF_MISMATCH = 0.9


def _where(item: ValueItem) -> str:
    return f"{item.symbol.path}:{item.line}"


def _supported(item: ValueItem, views: list[frozenset[str]]) -> bool:
    """A related name counts only if the words it lacks show up around it: `n_layers` says
    nothing about the *decoder* unless it sits in something called Decoder."""
    words = canon_words(item.name)
    context = canon_words(f"{item.symbol.qualname} {item.symbol.path}")
    for view in views:
        if words < view and (view - words) <= context:
            return True
        if view < words:  # the name is more specific than the subject: fine as support
            return True
    return False


def literal_check(ctx: CodeContext, claim: Claim) -> Decision | None:
    if claim.value is None:
        return None
    views = subject_views(claim.subject)
    if not views:
        return None
    exact: list[ValueItem] = []
    weak: list[ValueItem] = []
    for item in ctx.items:
        kind = name_match(item.name, views)
        if kind == "exact":
            exact.append(item)
        elif kind == "weak" and _supported(item, views):
            weak.append(item)

    comparable: list[tuple[ValueItem, bool]] = []
    for item in [*exact, *weak]:
        same = value_matches(claim.value, item.value, ctx.env)
        if same is not None:
            comparable.append((item, same))

    agreeing = next((it for it, same in comparable if same), None)
    conflict = next((it for it, same in comparable if not same and it in exact), None)
    if agreeing is not None and conflict is not None:
        # The code says both (a class default of 0.1, a config or CLI default of 0.0): which one
        # applies isn't a literal question. Leave it to the judge, which sees both spans.
        return None
    if agreeing is not None:
        return Decision(
            "matches",
            CONF_MATCH,
            "literal_match",
            f"{agreeing.name} = {agreeing.value} at {_where(agreeing)}.",
            [ctx.span(agreeing.symbol.path, agreeing.line)],
        )
    disagreeing = next((it for it, same in comparable if not same and it in exact), None)
    if disagreeing is not None:
        return Decision(
            "mismatch",
            CONF_MISMATCH,
            "literal_match",
            f"{disagreeing.name} = {disagreeing.value} at {_where(disagreeing)}.",
            [ctx.span(disagreeing.symbol.path, disagreeing.line)],
        )
    return None
