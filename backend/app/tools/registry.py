"""Tool registry: JSON schemas advertised to the model + a dispatch table.

Each tool has:
  * schema  - the JSON the LLM sees (name, description, parameters)
  * handler - handler(ctx, args) -> dict, which enforces access control
  * requires_confirmation - True for state-changing actions (prepare-only here)

The agent loop reads `requires_confirmation` to decide whether to execute a tool
inline (reads/calcs) or turn it into a pending action that the user must confirm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..auth import AuthContext
from . import documents, structured, actions


@dataclass
class ToolSpec:
    schema: dict
    handler: Callable[[AuthContext, dict], dict]
    requires_confirmation: bool = False


def _fn(schema: dict, handler, requires_confirmation: bool = False) -> ToolSpec:
    return ToolSpec(schema={"type": "function", "function": schema},
                    handler=handler, requires_confirmation=requires_confirmation)


def _opt_str(description: str) -> dict:
    """Optional string. Groq rejects `null` unless the schema explicitly allows it."""
    return {"type": ["string", "null"], "description": description}


# --- handlers (adapt model args dict -> typed calls) ------------------------

def _h_search(ctx, a):
    return documents.search_documents(ctx, a.get("query", ""), a.get("account_id"), a.get("top_k"))

def _h_get_account(ctx, a):
    return structured.get_account(ctx, a.get("account_id"))

def _h_get_order(ctx, a):
    return structured.get_order(ctx, a.get("order_id", ""))

def _h_list_tickets(ctx, a):
    return structured.list_tickets(ctx, a.get("account_id"), a.get("status"))

def _h_cancel(ctx, a):
    return structured.cancellation_eligibility(ctx, a.get("order_id", ""))

def _h_credit(ctx, a):
    return structured.service_credit_check(ctx, a.get("order_id", ""))

def _h_escalate(ctx, a):
    return actions.prepare_create_escalation(ctx, a.get("ticket_id", ""), a.get("reason", ""), a.get("severity"))

def _h_update_ticket(ctx, a):
    return actions.prepare_update_ticket(ctx, a.get("ticket_id", ""), a.get("status"), a.get("note"), a.get("assigned_to"))

def _h_followup(ctx, a):
    return actions.prepare_create_followup_task(ctx, a.get("title", ""), a.get("account_id"), a.get("due"), a.get("ticket_id"))


REGISTRY: dict[str, ToolSpec] = {
    "search_documents": _fn({
        "name": "search_documents",
        "description": "Search ParcelPilot policies, SOPs, product docs and customer agreements. "
                       "Use for any question about rules, entitlements, fees, SLAs, or known issues. "
                       "Returns passages tagged with an authority tier and citations.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look up, in natural language."},
            "account_id": _opt_str("Optional: focus on one account's contract (internal use)."),
        }, "required": ["query"]},
    }, _h_search),

    "get_account": _fn({
        "name": "get_account",
        "description": "Get an account record (plan, status, CSM, whether it has a signed agreement).",
        "parameters": {"type": "object", "properties": {
            "account_id": _opt_str("Account id, e.g. ACCT-001. Optional for customers."),
        }},
    }, _h_get_account),

    "get_order": _fn({
        "name": "get_order",
        "description": "Get one order/shipment by id (status, carrier, pickup window, fault flags, fee).",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order id, e.g. ORD-1001."},
        }, "required": ["order_id"]},
    }, _h_get_order),

    "list_tickets": _fn({
        "name": "list_tickets",
        "description": "List support tickets for an account (optionally filter by status). "
                       "Internal users may omit account_id to list across all accounts.",
        "parameters": {"type": "object", "properties": {
            "account_id": _opt_str("Account id. Omit to list all accounts (internal only)."),
            "status": _opt_str("Optional status filter, e.g. open."),
        }},
    }, _h_list_tickets),

    "cancellation_eligibility": _fn({
        "name": "cancellation_eligibility",
        "description": "Compute cancellation facts and the DEFAULT SOP outcome for an order (fee, free window). "
                       "If it reports contract_may_override, also read the customer's agreement before answering.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order id, e.g. ORD-1001."},
        }, "required": ["order_id"]},
    }, _h_cancel),

    "service_credit_check": _fn({
        "name": "service_credit_check",
        "description": "Compute failed-pickup delay and the DEFAULT service-credit outcome for an order. "
                       "If contract_may_override, read the customer's agreement before answering.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order id, e.g. ORD-2002."},
        }, "required": ["order_id"]},
    }, _h_credit),

    "create_escalation": _fn({
        "name": "create_escalation",
        "description": "Prepare an escalation for a ticket. Requires user confirmation before it is created.",
        "parameters": {"type": "object", "properties": {
            "ticket_id": {"type": "string"},
            "reason": {"type": "string", "description": "Why this needs escalation."},
            "severity": _opt_str("P1/P2/P3 if known."),
        }, "required": ["ticket_id", "reason"]},
    }, _h_escalate, requires_confirmation=True),

    "update_ticket": _fn({
        "name": "update_ticket",
        "description": "Prepare a ticket update (status/assignee/note). Requires user confirmation.",
        "parameters": {"type": "object", "properties": {
            "ticket_id": {"type": "string"},
            "status": _opt_str("New status, if changing."),
            "assigned_to": _opt_str("Assignee, if changing."),
            "note": _opt_str("Note to add."),
        }, "required": ["ticket_id"]},
    }, _h_update_ticket, requires_confirmation=True),

    "create_followup_task": _fn({
        "name": "create_followup_task",
        "description": "Prepare a follow-up task. Requires user confirmation before creation.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "due": _opt_str("Optional due date/time."),
            "ticket_id": _opt_str("Optional related ticket."),
            "account_id": _opt_str("Account id (internal use)."),
        }, "required": ["title"]},
    }, _h_followup, requires_confirmation=True),
}


def tool_schemas() -> list[dict]:
    """All tool schemas in the format Ollama expects."""
    return [spec.schema for spec in REGISTRY.values()]


def get_tool(name: str) -> ToolSpec | None:
    return REGISTRY.get(name)
