"""Thin, synchronous client for the Ollama HTTP API.

We keep this deliberately small and dependency-light (just httpx) so it is easy
to read. Two capabilities are used by the app:

  * embed_texts()  -> vectors for RAG (model: nomic-embed-text)
  * chat()         -> a chat completion that may include tool calls (llama3.1)
"""

from __future__ import annotations

from typing import Any

import httpx

from . import config

# Local models can be slow on first token; give generous timeouts.
_TIMEOUT = httpx.Timeout(300.0, connect=15.0)


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Return one embedding vector per input text.

    Tries the batch endpoint (/api/embed); falls back to the per-item endpoint
    (/api/embeddings) for older Ollama versions.
    """
    model = model or config.EMBED_MODEL
    if not texts:
        return []
    with httpx.Client(timeout=_TIMEOUT) as client:
        try:
            resp = client.post(
                f"{config.OLLAMA_HOST}/api/embed",
                json={"model": model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            if "embeddings" in data:
                return data["embeddings"]
        except (httpx.HTTPError, KeyError):
            pass  # fall through to per-item endpoint

        vectors: list[list[float]] = []
        for text in texts:
            resp = client.post(
                f"{config.OLLAMA_HOST}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
        return vectors


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Single non-streaming chat call. Returns the assistant `message` dict.

    The returned message may contain `tool_calls` when the model wants to invoke
    a tool. We keep temperature low for consistent, less "creative" support answers.
    """
    model = model or config.CHAT_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(f"{config.OLLAMA_HOST}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json().get("message", {})


def health() -> bool:
    """Return True if the Ollama server responds."""
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
            return client.get(f"{config.OLLAMA_HOST}/api/tags").status_code == 200
    except httpx.HTTPError:
        return False
