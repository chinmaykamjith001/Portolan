from __future__ import annotations

from portolan.llm.cache import LLMCache, cache_key, inputs_hash
from portolan.llm.types import Usage
from portolan.store.db import Database


def test_inputs_hash_is_order_and_whitespace_insensitive() -> None:
    a = inputs_hash({"b": [1, 2], "a": "hello  \r\nworld \n"})
    b = inputs_hash({"a": "hello\nworld", "b": [1, 2]})
    assert a == b
    assert inputs_hash({"a": "hello"}) != inputs_hash({"a": "hullo"})


def test_cache_key_depends_on_every_part() -> None:
    base = cache_key("m", "t", 1, "h")
    assert base != cache_key("m2", "t", 1, "h")
    assert base != cache_key("m", "t2", 1, "h")
    assert base != cache_key("m", "t", 2, "h")
    assert base != cache_key("m", "t", 1, "h2")


def test_put_get_and_invalidate_stale_versions(db: Database) -> None:
    cache = LLMCache(db)
    usage = Usage(input_tokens=3, output_tokens=4)
    for version in (1, 2):
        cache.put(
            f"k{version}",
            model="m",
            template_id="explain",
            template_version=version,
            text=f"v{version}",
            usage=usage,
        )
    cache.put(
        "other", model="m", template_id="edge_explain", template_version=1, text="x", usage=usage
    )

    hit = cache.get("k1")
    assert hit is not None and hit.text == "v1" and hit.usage == usage
    assert cache.get("missing") is None

    assert cache.invalidate_stale("explain", current_version=2) == 1
    assert cache.get("k1") is None
    assert cache.get("k2") is not None
    assert cache.get("other") is not None  # other templates untouched
