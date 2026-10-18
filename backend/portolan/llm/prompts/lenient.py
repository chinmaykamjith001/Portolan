"""Forgiving fields for LLM replies: a near-miss enum value must not throw away a whole reply.

The JSON Schema still lists the allowed values (that is what steers the model); validation maps
casing/spacing variants to the allowed value and anything unknown to a safe default.
"""

from __future__ import annotations

from typing import Any, cast, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict


def lenient(literal: Any, default: str) -> BeforeValidator:
    allowed: tuple[str, ...] = get_args(literal)

    def fix(value: object) -> object:
        if isinstance(value, str):
            norm = value.strip().lower().replace("-", "_").replace(" ", "_")
            if norm in allowed:
                return norm
        return default

    return BeforeValidator(fix)


def lenient_float(default: float = 0.5) -> BeforeValidator:
    """Confidence-like numbers: 0.8, "0.8" or "80%"; clamped to 0..1; default if unreadable."""

    def fix(value: object) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, int | float):
            number = float(value)
        elif isinstance(value, str):
            text = value.strip().rstrip("%")
            try:
                number = float(text) / (100 if value.strip().endswith("%") else 1)
            except ValueError:
                return default
        else:
            return default
        return min(1.0, max(0.0, number))

    return BeforeValidator(fix)


def _require_all(schema: dict[str, Any]) -> None:
    """List every property as required in the JSON Schema sent to the model. Gemini tends to
    leave out optional fields (no `notes`, no `anchors`), which drops otherwise good answers;
    our own validation keeps the defaults, so a reply that still omits one stays usable."""
    props = schema.get("properties")
    if isinstance(props, dict):
        schema["required"] = list(cast(dict[str, Any], props))


class ReplySchema(BaseModel):
    """Base for structured LLM replies: every field is required in the schema the model sees."""

    model_config = ConfigDict(json_schema_extra=_require_all)
