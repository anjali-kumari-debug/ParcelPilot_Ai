"""Chat dispatcher.

Default: Groq. Local Ollama is used only when ENABLE_OLLAMA=true.
"""

from __future__ import annotations

from typing import Any

from . import config
from . import groq_client

VALID_PROVIDERS = ("groq", "ollama")


def ollama_enabled() -> bool:
    return bool(config.ENABLE_OLLAMA)


def normalize_provider(provider: str | None) -> str:
    """Resolve the chat backend. Ollama is ignored unless ENABLE_OLLAMA=true."""
    if not config.ENABLE_OLLAMA:
        return "groq"
    p = (provider or config.LLM_PROVIDER or "groq").lower()
    return p if p in VALID_PROVIDERS else "groq"


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if normalize_provider(provider) == "ollama":
        from . import ollama_client

        return ollama_client.chat(messages, tools=tools, **kwargs)
    return groq_client.chat(messages, tools=tools, **kwargs)
