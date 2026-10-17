"""LLM providers. Import them only from `portolan.llm.gateway`."""

from __future__ import annotations

from portolan.config import Config
from portolan.llm.providers.base import CallContext, FixtureStore, Provider
from portolan.llm.providers.litellm_provider import LiteLLMProvider
from portolan.llm.providers.mock import MockProvider


def make_provider(config: Config) -> Provider:
    match config.llm.provider:
        case "mock":
            return MockProvider()
        case "gemini":
            return LiteLLMProvider("gemini")
        case "ollama":
            return LiteLLMProvider("ollama", api_base=config.llm.ollama_base_url)


__all__ = [
    "CallContext",
    "FixtureStore",
    "LiteLLMProvider",
    "MockProvider",
    "Provider",
    "make_provider",
]
