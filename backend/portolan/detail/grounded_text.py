"""Keeps LLM-typed numbers out of the UI (spec §10.7.3: numbers come from the sandbox).

A caption or note may only contain numbers that the executed program produced (in its table or
data) or that appear in the program's own source. Anything else is dropped by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# A standalone number: not glued to letters/underscores/digits/dots, so "d_k", "x2", "v1.5" and
# "3rd" are not numbers. Thousands separators ("1,000") and exponents ("1e-5") are one number.
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:[eE][+-]?\d+)?(?!\w)")


def numbers_in(text: str) -> list[tuple[float, int]]:
    """(absolute value, decimals written) for each standalone number in `text`."""
    found: list[tuple[float, int]] = []
    for m in NUMBER.finditer(text):
        try:
            value = float(m.group(0).replace(",", ""))
        except ValueError:
            continue
        found.append((value, len(m.group(2)) - 1 if m.group(2) else 0))
    return found


class NumberGuard:
    """The numbers a run is allowed to talk about."""

    def __init__(self, texts: Iterable[str] = (), values: Iterable[float] = ()) -> None:
        self.values: set[float] = {abs(v) for t in texts for v, _ in numbers_in(t)}
        self.values.update(abs(v) for v in values)

    def allows(self, value: float, decimals: int) -> bool:
        for known in self.values:
            if abs(known - value) <= 1e-9 * max(1.0, known):
                return True
            # "0.66" is fine when the run produced 0.659; "3" is not fine for 2.718.
            if decimals > 0 and abs(round(known, decimals) - value) <= 1e-9:
                return True
        return False

    def check(self, text: str | None) -> str | None:
        """`text` if every number in it comes from the run, else None."""
        if text is None:
            return None
        for value, decimals in numbers_in(text):
            if not self.allows(value, decimals):
                return None
        return text
