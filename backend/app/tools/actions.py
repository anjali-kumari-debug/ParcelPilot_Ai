"""State-changing action tools (MOCKED) with a two-step prepare/execute design.

Every action here changes state, so it must be confirmed by the user first
(requirement #4). We therefore split each action into:

  * prepare_*  -> validates access + builds a *proposal* (nothing is written)
  * execute_action -> runs ONLY after the user confirms, writes to `actions`
                      (and updates the ticket for ticket updates)

The agent loop calls the prepare_* functions. It never writes directly; it
surfaces the proposal to the UI, waits for /api/confirm, then calls execute_action.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..auth import AuthContext
from ..db import get_conn
from .. import config


def _new_action_id() -> str:
    return "ACT-" + uuid.uuid4().hex[:8].upper()


def _fetch_ticket(ticket_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        return {k: row[k] for k in row.keys()} if row else None


def _proposal(action_type: str, account_id: str | None, target_id: str | None,
              summary: str, payload: dict) -> dict[str, Any]:
    return {
        "action_id": _new_action_id(),
        "action_type": action_type,
        "account_id": account_id,
        "target_id": target_id,
        "summary": summary,
        "payload": payload,
        "requires_confirmation": True,
        "status": "prepared",
    }


# --- prepare_* (no writes) --------------------------------------------------

def prepare_create_escalation(ctx: AuthContext, ticket_id: str, reason: str,
                              severity: str | None = None) -> dict:
    ticket = _fetch_ticket(ticket_id)
    if not ticket:
        return {"error": "not_found", "message": f"No ticket {ticket_id}."}
    if not ctx.can_access_account(ticket.get("account_id")):
        return {"error": "access_denied", "message": "You cannot act on that ticket."}
    payload = {"ticket_id": ticket_id, "reason": reason, "severity": severity,
               "subject": ticket.get("subject")}
    summary = f"Escalate {ticket_id} ({ticket.get('subject')}) - severity {severity or 'unset'}: {reason}"
    return _proposal("create_escalation", ticket.get("account_id"), ticket_id, summary, payload)


def prepare_update_ticket(ctx: AuthContext, ticket_id: str, status: str | None = None,
                         note: str | None = None, assigned_to: str | None = None) -> dict:
    ticket = _fetch_ticket(ticket_id)
    if not ticket:
        return {"error": "not_found", "message": f"No ticket {ticket_id}."}
    if not ctx.can_access_account(ticket.get("account_id")):
        return {"error": "access_denied", "message": "You cannot act on that ticket."}
    changes = {k: v for k, v in {"status": status, "assigned_to": assigned_to, "note": note}.items() if v}
    if not changes:
        return {"error": "no_changes", "message": "Specify at least one field to update."}
    summary = f"Update {ticket_id}: " + ", ".join(f"{k}={v}" for k, v in changes.items())
    return _proposal("update_ticket", ticket.get("account_id"), ticket_id, summary, changes)


def prepare_create_followup_task(ctx: AuthContext, title: str, account_id: str | None = None,
                                due: str | None = None, ticket_id: str | None = None) -> dict:
    target_account = ctx.scope_account(account_id)
    if ctx.is_customer and target_account != ctx.account_id:
        return {"error": "access_denied", "message": "You cannot create tasks for another account."}
    payload = {"title": title, "due": due, "ticket_id": ticket_id}
    summary = f"Follow-up task: {title}" + (f" (due {due})" if due else "")
    return _proposal("create_followup_task", target_account, ticket_id, summary, payload)


# --- execute (writes; called only after confirmation) -----------------------

def execute_action(ctx: AuthContext, proposal: dict) -> dict:
    """Persist a confirmed action to the `actions` table (mocked side effect)."""
    action_type = proposal.get("action_type")
    account_id = proposal.get("account_id")

    # Re-check authorisation at execution time (defence in depth).
    if ctx.is_customer and account_id != ctx.account_id:
        return {"error": "access_denied", "message": "Not authorised to execute this action."}

    created_at = config.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO actions (action_id, action_type, account_id, target_id, payload, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                proposal["action_id"], action_type, account_id, proposal.get("target_id"),
                json.dumps(proposal.get("payload", {})), ctx.user_name, created_at,
            ),
        )
        # For ticket updates, also reflect the change on the ticket row.
        if action_type == "update_ticket":
            payload = proposal.get("payload", {})
            sets, params = [], []
            for field in ("status", "assigned_to"):
                if payload.get(field):
                    sets.append(f"{field} = ?")
                    params.append(payload[field])
            if sets:
                params.append(proposal["target_id"])
                conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE ticket_id = ?", params)

    return {
        "status": "executed",
        "action_id": proposal["action_id"],
        "action_type": action_type,
        "message": f"{action_type} recorded (mocked) as {proposal['action_id']}.",
        "created_at": created_at,
    }
