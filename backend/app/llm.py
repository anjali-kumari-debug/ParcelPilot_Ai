"""Provider-agnostic chat dispatcher.

The agent loop calls `llm.chat(...)` without caring whether the answer comes
from the local Ollama runtime or the Groq free cloud tier. Both underlying
clients return the same message shape, so switching is a one-line routing
decision here (driven by a per-request `provider` or the configured default).
"""

from __future__ import annotations

from typing import Any

from . import config
from . import ollama_client
from . import groq_client

VALID_PROVIDERS = ("ollama", "groq")


def normalize_provider(provider: str | None) -> str:
    """Return a supported provider name, falling back to the configured default."""
    p = (provider or config.LLM_PROVIDER or "groq").lower()
    return p if p in VALID_PROVIDERS else "groq"


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Route a chat call to the selected provider (local Ollama or cloud Groq)."""
    if normalize_provider(provider) == "groq":
        return groq_client.chat(messages, tools=tools, **kwargs)
    return ollama_client.chat(messages, tools=tools, **kwargs)
