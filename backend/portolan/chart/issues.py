"""Issue roll-up (spec §10.5.4): badges count distinct findings anywhere beneath a concept."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from portolan.models.concept import Concept

# A concept whose layer is still being produced, or failed, is not a place to pin a finding.
_UNGENERATED = ("generating", "failed")


def issues_inside(
    children: Callable[[str], Iterable[str]],
    findings_of: Callable[[str], Iterable[str]],
    concept_id: str,
) -> int:
    """Distinct finding ids attached to any descendant of `concept_id`, excluding its own.

    Iterative DFS with a visited set: each node is expanded and queried once per call, and a
    cycle (the graph should be a DAG, but data is data) cannot loop forever.
    """
    found: set[str] = set()
    visited = {concept_id}
    stack = list(children(concept_id))
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        found.update(findings_of(node))
        stack.extend(c for c in children(node) if c not in visited)
    return len(found)


def nearest_ancestor_by_anchor(
    finding_anchors: set[str],
    concepts: Mapping[str, Concept],
    parent_of: Callable[[str], str | None],
) -> str | None:
    """The concept a finding belongs to: the deepest generated concept whose anchors overlap.

    A matching concept that isn't generated yet (`generating`/`failed`) is lifted to its nearest
    generated ancestor via `parent_of`. Among equally deep matches the larger overlap wins, then
    the smaller id, so the result is deterministic.
    """
    if not finding_anchors:
        return None
    best: tuple[int, int, str] | None = None  # (-depth, -overlap, id): min() is the winner
    for cid, c in concepts.items():
        overlap = len(finding_anchors.intersection(c.anchors))
        if not overlap:
            continue
        target = _generated_ancestor(cid, concepts, parent_of)
        if target is None:
            continue
        key = (-concepts[target].depth, -overlap, target)
        if best is None or key < best:
            best = key
    return best[2] if best else None


def _generated_ancestor(
    cid: str, concepts: Mapping[str, Concept], parent_of: Callable[[str], str | None]
) -> str | None:
    seen: set[str] = set()
    node: str | None = cid
    while node is not None and node not in seen:
        seen.add(node)
        c = concepts.get(node)
        if c is not None and c.gen_state not in _UNGENERATED:
            return node
        node = parent_of(node)
    return None
