"""Embedding helper for RAG indexing/query and ticket clustering.

Default: in-process fastembed (CPU, no extra server).
When ENABLE_OLLAMA=true and EMBED_PROVIDER=ollama, vectors come from Ollama.
Indexing and querying must use the same provider; the index is rebuilt on boot.
"""

from __future__ import annotations

from typing import Iterable

from . import config

_fastembed_model = None


def _get_fastembed():
    global _fastembed_model
    if _fastembed_model is None:
        from fastembed import TextEmbedding

        _fastembed_model = TextEmbedding(model_name=config.FASTEMBED_MODEL)
    return _fastembed_model


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    """Return one embedding vector (list[float]) per input text."""
    items = list(texts)
    if not items:
        return []

    if config.ENABLE_OLLAMA and config.EMBED_PROVIDER == "ollama":
        from .ollama_client import embed_texts as _ollama_embed

        return _ollama_embed(items)

    model = _get_fastembed()
    return [[float(x) for x in vec] for vec in model.embed(items)]
