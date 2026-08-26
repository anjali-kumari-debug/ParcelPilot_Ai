"""The agent loop: LLM + tools + a bounded loop, streamed as structured events.

Event types yielded (consumed by the API -> SSE -> UI):
  * {"type": "tool_call",   "name", "arguments"}
  * {"type": "tool_result", "name", "result"}
  * {"type": "pending_action", "action": <proposal>}   # pause for confirmation
  * {"type": "action_executed" / "action_cancelled", ...}
  * {"type": "message", "content", "citations", "confidence", "trust_notes"}
  * {"type": "error", "message"}
  * {"type": "done"}

The `messages` list is mutated in place so the caller (session store) keeps the
full transcript for follow-up turns and for confirmation resume.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from ..auth import AuthContext, find_foreign_account_refs
from .. import llm
from ..tools.registry import tool_schemas, get_tool
from ..tools import actions
from .. import config
from . import trust

_FALLBACK = (
    "I couldn't complete this confidently with the tools and sources available, "
    "so I'm escalating it to a human on the ParcelPilot support team rather than "
    "guessing."
)

_EGRESS_NOTE = "Blocked an outbound answer that referenced another account (egress access-control)."


def _cross_account_refusal(ctx: AuthContext) -> str:
    """Canonical refusal shared by the ingress and egress access-control gates."""
    return (
        f"I can only access {ctx.user_name}'s own account ({ctx.account_id}) - "
        "its orders, tickets, and agreement. I can't share another customer's "
        "account details or contract terms. If you need information about a "
        "different account, please have an authorised ParcelPilot operations "
        "user assist you."
    )


def _egress_refusal(ctx: AuthContext, content: str, retrieved: list[dict]) -> str | None:
    """Layer 2 - egress attribution gate.

    Access control is enforced at retrieval time, but the model can still take
    the caller's OWN (correctly scoped) sources and narrate them as if they
    belonged to another customer. This gate inspects the OUTBOUND answer and
    refuses if, for a customer, it either
      * names another account (by id or brand, spacing-robust), or
      * was somehow grounded in a source owned by another account.
    Internal users may speak across accounts, so this never fires for them.
    """
    if not ctx.is_customer:
        return None
    if find_foreign_account_refs(ctx, content or ""):
        return _cross_account_refusal(ctx)
    for hit in retrieved or []:
        acct = hit.get("account_id")
        if acct and acct not in ("ALL", ctx.account_id):
            return _cross_account_refusal(ctx)
    return None


def _parse_args(raw: Any) -> dict:
    """Ollama usually returns a dict; be tolerant of a JSON string (repair once)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Lenient repair: single -> double quotes.
            try:
                return json.loads(raw.replace("'", '"'))
            except json.JSONDecodeError:
                return {}
    return {}


def _tool_msg(name: str, obj: Any) -> dict:
    return {"role": "tool", "name": name, "content": json.dumps(obj, default=str)}


_OVERRIDE_QUERIES = {
    "cancellation_eligibility": "cancellation fee waiver for BOOKED shipment before pickup",
    "service_credit_check": "failed pickup service credit threshold and amount",
}


def run(ctx: AuthContext, messages: list[dict], provider: str | None = None) -> Iterator[dict]:
    """Drive the tool-calling loop until a final answer or a pending action.

    `provider` selects the LLM backend ("ollama" local or "groq" cloud);
    None falls back to the configured default.
    """
    # Access-control guard (defence in depth): if a customer's request names
    # another account, refuse in code before any model/tool work. The tool layer
    # already scopes reads, but this stops the model from answering a "what are
    # <other customer>'s terms" question using this customer's own sources.
    last = messages[-1] if messages else {}
    if (ctx.is_customer and last.get("role") == "user"
            and not str(last.get("content", "")).startswith("[system]")):
        foreign = find_foreign_account_refs(ctx, str(last.get("content", "")))
        if foreign:
            refusal = _cross_account_refusal(ctx)
            messages.append({"role": "assistant", "content": refusal})
            yield {
                "type": "message",
                "content": refusal,
                "citations": [],
                "confidence": "high",
                "trust_notes": ["Blocked a cross-account request in the access-control layer."],
            }
            yield {"type": "done"}
            return

    tools = tool_schemas()
    retrieved: list[dict] = []  # accumulate document hits for citations/trust

    # Reliability guard: a small local model often stops after the calculator and
    # ignores contract_may_override. We force it to read the agreement before it
    # is allowed to give a final number.
    contract_override_flagged = False
    did_search = False
    override_query = ""
    forced_nudges = 0

    for _step in range(config.AGENT_MAX_STEPS):
        try:
            msg = llm.chat(messages, tools=tools, provider=provider)
        except Exception as exc:  # network / model error
            # region agent log
            from .._debug import dlog
            dlog("agent/loop.py:run", "LLM call failed",
                 {"provider": provider, "step": _step, "error": repr(exc)}, hypothesis="E")
            # endregion
            yield {"type": "error", "message": f"Model call failed: {exc}"}
            yield {"type": "done"}
            return

        tool_calls = msg.get("tool_calls") or []
        # region agent log
        from .._debug import dlog
        dlog("agent/loop.py:run", "LLM step",
             {"provider": provider, "step": _step,
              "tool_names": [c.get("function", {}).get("name") for c in tool_calls],
              "did_search": did_search, "contract_override_flagged": contract_override_flagged,
              "final_content_len": len(msg.get("content") or "") if not tool_calls else None},
             hypothesis="C")
        # endregion
        # Record the assistant turn (with any tool_calls) in the transcript.
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content", "")}
        if tool_calls:
            assistant_entry["tool_calls"] = tool_calls
        messages.append(assistant_entry)

        if not tool_calls:
            # Guard: don't let the model finalise a fee/credit before it has read
            # the customer's agreement that may override the default.
            if contract_override_flagged and not did_search and forced_nudges < 2:
                forced_nudges += 1
                messages.pop()  # drop this premature "final" turn
                messages.append({
                    "role": "user",
                    "content": "[system] The calculator reported contract_may_override=true, "
                               "so a signed customer agreement may change this. Before you answer, "
                               f"call search_documents (e.g. query: '{override_query}') to read the "
                               "agreement, then base the final fee/credit on it if it applies. "
                               "Do not state a final number yet, and never invent a source.",
                })
                continue

            content = msg.get("content", "")
            refusal = _egress_refusal(ctx, content, retrieved)
            if refusal is not None:
                # Scrub the transcript so the leaked wording never persists into
                # a follow-up turn, then return the canonical refusal instead.
                messages[-1]["content"] = refusal
                yield {
                    "type": "message",
                    "content": refusal,
                    "citations": [],
                    "confidence": "high",
                    "trust_notes": [_EGRESS_NOTE],
                }
                yield {"type": "done"}
                return

            analysis = trust.analyze(retrieved)
            yield {
                "type": "message",
                "content": content,
                "citations": analysis["citations"],
                "confidence": analysis["confidence"],
                "trust_notes": analysis["notes"],
            }
            yield {"type": "done"}
            return

        pending: dict | None = None
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = _parse_args(fn.get("arguments"))
            yield {"type": "tool_call", "name": name, "arguments": args}

            spec = get_tool(name)
            if spec is None:
                result = {"error": "unknown_tool", "message": f"No tool named '{name}'."}
                messages.append(_tool_msg(name, result))
                yield {"type": "tool_result", "name": name, "result": result}
                continue

            try:
                result = spec.handler(ctx, args)
            except Exception as exc:
                result = {"error": "tool_exception", "message": str(exc)}

            if name == "search_documents" and isinstance(result, dict) and "results" in result:
                retrieved.extend(result["results"])
                did_search = True
            if (name in _OVERRIDE_QUERIES and isinstance(result, dict)
                    and result.get("contract_may_override")):
                contract_override_flagged = True
                override_query = _OVERRIDE_QUERIES[name]

            if spec.requires_confirmation and "error" not in result:
                # Prepared, but NOT executed. Pair the tool call with a response so
                # the transcript stays valid, then pause the loop.
                pending = result
                messages.append(_tool_msg(name, {"status": "prepared_awaiting_confirmation", "proposal": result}))
                yield {"type": "tool_result", "name": name, "result": result}
            else:
                messages.append(_tool_msg(name, result))
                yield {"type": "tool_result", "name": name, "result": result}

        if pending is not None:
            yield {"type": "pending_action", "action": pending}
            yield {"type": "done"}
            return

    # Loop exhausted: bias toward escalation rather than a confident guess.
    analysis = trust.analyze(retrieved)
    yield {
        "type": "message",
        "content": _FALLBACK,
        "citations": analysis["citations"],
        "confidence": "low",
        "trust_notes": ["Reached the reasoning-step limit; escalated to a human."],
    }
    yield {"type": "done"}


def _final_turn(ctx: AuthContext, messages: list[dict], provider: str | None = None) -> Iterator[dict]:
    """Ask the model for a closing message WITHOUT tools (used after confirmation)."""
    try:
        msg = llm.chat(messages, tools=None, provider=provider)
    except Exception as exc:
        yield {"type": "error", "message": f"Model call failed: {exc}"}
        yield {"type": "done"}
        return
    content = msg.get("content", "")
    refusal = _egress_refusal(ctx, content, [])
    if refusal is not None:
        messages.append({"role": "assistant", "content": refusal})
        yield {"type": "message", "content": refusal, "citations": [],
               "confidence": "high", "trust_notes": [_EGRESS_NOTE]}
        yield {"type": "done"}
        return
    messages.append({"role": "assistant", "content": content})
    yield {"type": "message", "content": content, "citations": [], "confidence": "n/a", "trust_notes": []}
    yield {"type": "done"}


def confirm(ctx: AuthContext, messages: list[dict], proposal: dict, approved: bool,
            provider: str | None = None) -> Iterator[dict]:
    """Resume after the user confirms/declines a prepared action."""
    if approved:
        exec_result = actions.execute_action(ctx, proposal)
        yield {"type": "action_executed", "result": exec_result}
        messages.append({
            "role": "user",
            "content": f"[system] The user CONFIRMED action {proposal['action_id']}. "
                       f"It was executed: {exec_result.get('message')}. "
                       f"Give a brief final confirmation to the user. Do not call any tools.",
        })
    else:
        yield {"type": "action_cancelled", "action_id": proposal.get("action_id")}
        messages.append({
            "role": "user",
            "content": f"[system] The user DECLINED action {proposal.get('action_id')}. "
                       f"Do not perform it. Acknowledge and offer alternatives. Do not call any tools.",
        })
    yield from _final_turn(ctx, messages, provider=provider)
