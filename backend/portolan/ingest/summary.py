"""Human-readable summaries of a PaperDoc (used by `portolan ingest --dry-run`)."""

from __future__ import annotations

from collections import Counter

from portolan.models.paper import PaperDoc


def nearest_section(doc: PaperDoc) -> dict[str, str | None]:
    """anchor id → id of the section that contains it (directly or via a paragraph)."""
    by_id = {a.id: a for a in doc.anchors}
    out: dict[str, str | None] = {}
    for a in doc.anchors:
        parent = a.parent_id
        while parent is not None and by_id[parent].kind != "section":
            parent = by_id[parent].parent_id
        out[a.id] = parent
    return out


def section_tree(doc: PaperDoc) -> list[str]:
    owner = nearest_section(doc)
    counts: dict[str | None, Counter[str]] = {}
    for a in doc.anchors:
        if a.kind != "section":
            counts.setdefault(owner[a.id], Counter())[a.kind] += 1
    short = {
        "paragraph": "¶",
        "equation": "eq",
        "table": "tab",
        "figure": "fig",
        "algorithm": "alg",
    }
    lines: list[str] = []
    for a in doc.by_kind("section"):
        c = counts.get(a.id, Counter())
        detail = ", ".join(f"{c[k]} {v}" for k, v in short.items() if c[k])
        label = a.label if a.label != a.text else ""
        head = f"{label}  {a.text}".strip()
        lines.append(f"{'  ' * ((a.level or 1) - 1)}{head}" + (f"  ({detail})" if detail else ""))
    return lines


def anchor_counts(doc: PaperDoc) -> dict[str, int]:
    c = Counter(a.kind for a in doc.anchors)
    return {k: c[k] for k in ("section", "paragraph", "equation", "table", "figure", "algorithm")}
