"""Thin client for Groq's OpenAI-compatible chat API (a free cloud LLM).

This is the "cloud" alternative to the local Ollama runtime. It exposes a
`chat()` with the SAME return shape as `ollama_client.chat()` so the agent loop
does not care which provider produced the message.

Groq speaks the OpenAI wire format, which differs from Ollama in two ways the
agent loop cares about:

  * tool-call responses (`role: "tool"`) must carry a `tool_call_id`, and
  * assistant tool-calls must carry an `id` + `type: "function"`.

Our transcript is stored in the simpler Ollama shape, so we normalise it to the
OpenAI shape at send time (see `_to_openai_messages`). Embeddings are NOT
provided by Groq; RAG keeps using Ollama's `nomic-embed-text`.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from . import config

# Cloud round-trips are usually fast, but keep a generous ceiling for multi-tool turns.
_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
_ENDPOINT = "/openai/v1/chat/completions"

# Free-tier rate-limit (HTTP 429) handling. Groq tells us how long to wait, either
# via a Retry-After header or in the error body ("try again in 16.725s").
_MAX_RETRIES = 4
_RETRY_WAIT_CAP = 30.0  # never sleep longer than this on a single retry
_RETRY_AFTER_RE = re.compile(r"try again in ([0-9.]+)s")


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a 429, per the provider's guidance."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(float(header) + 0.5, _RETRY_WAIT_CAP)
        except ValueError:
            pass
    match = _RETRY_AFTER_RE.search(resp.text or "")
    if match:
        try:
            return min(float(match.group(1)) + 0.5, _RETRY_WAIT_CAP)
        except ValueError:
            pass
    return min(2.0 * (attempt + 1), _RETRY_WAIT_CAP)  # exponential-ish fallback


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Ollama-style transcript messages to the OpenAI/Groq shape.

    The key fix-ups: synthesise stable ids for assistant tool-calls and attach
    the matching `tool_call_id` to each following `role: "tool"` message (paired
    by transcript order, which is how the loop appends them).
    """
    out: list[dict[str, Any]] = []
    pending_ids: list[str] = []  # tool_call ids awaiting a tool response, in order
    counter = 0

    for m in messages:
        role = m.get("role")

        if role == "assistant" and m.get("tool_calls"):
            tool_calls = []
            pending_ids = []
            for tc in m["tool_calls"]:
                cid = tc.get("id") or f"call_{counter}"
                counter += 1
                fn = tc.get("function", {}) or {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args)
                tool_calls.append({
                    "id": cid,
                    "type": "function",
                    "function": {"name": fn.get("name", ""), "arguments": args or "{}"},
                })
                pending_ids.append(cid)
            out.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": tool_calls,
            })

        elif role == "tool":
            cid = pending_ids.pop(0) if pending_ids else f"call_{counter}"
            if not pending_ids:
                counter += 1
            out.append({
                "role": "tool",
                "tool_call_id": cid,
                "content": m.get("content", ""),
            })

        else:
            out.append({"role": role, "content": m.get("content", "") or ""})

    return out


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Single non-streaming chat call. Returns the assistant `message` dict.

    The returned dict matches Ollama's shape closely enough for the agent loop:
    it may contain `tool_calls`, each with a `function.name` and (string)
    `function.arguments`, which the loop's `_parse_args` already tolerates.
    """
    if not config.GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your environment (or backend/.env) "
            "to use the cloud model."
        )

    model = model or config.GROQ_CHAT_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(messages),
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # region agent log
    from ._debug import dlog
    _oai = payload["messages"]
    dlog("groq_client.py:chat", "groq request shape",
         {"model": model, "n_messages": len(_oai),
          "n_tool_role_msgs": sum(1 for m in _oai if m.get("role") == "tool"),
          "n_assistant_tool_calls": sum(len(m.get("tool_calls", [])) for m in _oai if m.get("role") == "assistant"),
          "has_tools": bool(tools)}, hypothesis="B")
    # endregion
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
    url = f"{config.GROQ_BASE_URL}{_ENDPOINT}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        for attempt in range(_MAX_RETRIES):
            resp = client.post(url, json=payload, headers=headers)

            # Transient rate limit: wait the provider-specified interval and retry.
            if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
                wait = _retry_after_seconds(resp, attempt)
                # region agent log
                dlog("groq_client.py:chat", "groq 429 - backing off and retrying",
                     {"attempt": attempt, "wait_s": wait, "body": (resp.text or "")[:300]},
                     hypothesis="E")
                # endregion
                time.sleep(wait)
                continue

            # region agent log
            if resp.status_code >= 400:
                dlog("groq_client.py:chat", "groq HTTP error",
                     {"status": resp.status_code, "attempt": attempt,
                      "body": resp.text[:600]}, hypothesis="E")
            # endregion
            resp.raise_for_status()
            data = resp.json()
            break

    choices = data.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": ""}
    return choices[0].get("message", {}) or {"role": "assistant", "content": ""}


def health() -> bool:
    """Return True if a Groq API key is configured (cheap check, no network call)."""
    return bool(config.GROQ_API_KEY)
