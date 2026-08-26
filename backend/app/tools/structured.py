"""Structured-data tools: account/order/ticket lookups + policy calculators.

Design notes
------------
* Access control: every function takes an `AuthContext`. Customers are pinned to
  their own account in the SQL WHERE clause; a customer asking about another
  account's order simply gets "not found / access denied" - the data never leaves
  the tool layer.
* Separation of facts vs policy: the calculators compute *facts* (time deltas,
  fault flags) and the *default SOP outcome*. When the account has a signed
  agreement, they flag `contract_may_override=True` so the agent knows it must
  read that contract (via the document tool) before giving a final answer. This
  keeps the authoritative numbers in the documents, not hard-coded here.
"""

from __future__ import annotations

from typing import Any

from ..auth import AuthContext
from ..db import get_conn
from ..util import parse_dt, to_bool, minutes_between
from .. import config

# Default numbers from Cancellation & Service Credit SOP v4 (the general policy).
FREE_CANCEL_WINDOW_MIN = 30
DEFAULT_CANCEL_FEE_INR = 250
DEFAULT_CREDIT_DELAY_MIN = 120           # "more than 2 hours"
DEFAULT_CREDIT_CAP_INR = 500
DEFAULT_CREDIT_RATE = 0.10               # 10% of shipment fee
MANAGER_APPROVAL_ABOVE_INR = 1000


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _fetch_account(account_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        return _row_to_dict(row) if row else None


def _fetch_order(order_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return _row_to_dict(row) if row else None


# --- Read tools -------------------------------------------------------------

def get_account(ctx: AuthContext, account_id: str | None = None) -> dict:
    """Return an account record. Customers can only see their own account."""
    target = ctx.scope_account(account_id)
    if target is None:
        return {"error": "no_account_specified", "message": "Please specify an account_id."}
    if not ctx.can_access_account(target):
        return {"error": "access_denied", "message": "You are not authorised to view that account."}
    acct = _fetch_account(target)
    if not acct:
        return {"error": "not_found", "message": f"No account {target}."}
    # Include whether a contract exists (drives the "check the agreement" step).
    acct["has_contract"] = bool(acct.get("contract_file"))
    return acct


def get_order(ctx: AuthContext, order_id: str) -> dict:
    """Return an order. Enforces that the order belongs to an account the caller may see."""
    order = _fetch_order(order_id)
    if not order:
        return {"error": "not_found", "message": f"No order {order_id}."}
    if not ctx.can_access_account(order.get("account_id")):
        # Do not leak existence details across accounts.
        return {"error": "access_denied", "message": "You are not authorised to view that order."}
    return order


def list_tickets(ctx: AuthContext, account_id: str | None = None, status: str | None = None) -> dict:
    """List tickets, scoped to the caller. Customers see only their own account."""
    target = ctx.scope_account(account_id)
    clauses, params = [], []
    if target is not None:
        clauses.append("account_id = ?")
        params.append(target)
    elif ctx.is_customer:
        return {"error": "access_denied", "message": "Customers must query their own account."}
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM tickets{where} ORDER BY created_at", params).fetchall()
    return {"count": len(rows), "tickets": [_row_to_dict(r) for r in rows]}


# --- Calculators (perform the numeric reasoning) ---------------------------

def cancellation_eligibility(ctx: AuthContext, order_id: str) -> dict:
    """Compute cancellation facts + the DEFAULT SOP outcome for an order.

    Returns enough for the agent to decide, and flags when a customer agreement
    might override the default (agent must then read that contract).
    """
    order = get_order(ctx, order_id)
    if "error" in order:
        return order

    status = (order.get("status") or "").upper()
    booked = parse_dt(order.get("booked_at"))
    requested = parse_dt(order.get("cancellation_requested_at"))
    picked_up = parse_dt(order.get("pickup_actual_at")) is not None or status == "PICKED_UP"

    mins_booking_to_request = minutes_between(requested, booked)
    within_free_window = (mins_booking_to_request is not None and mins_booking_to_request <= FREE_CANCEL_WINDOW_MIN)

    acct = _fetch_account(order.get("account_id")) or {}
    has_contract = bool(acct.get("contract_file"))

    # Default SOP outcome (before any contract override).
    if status == "DRAFT":
        default = {"cancellable": True, "fee_inr": 0, "reason": "DRAFT orders cancel free."}
    elif status == "DELIVERED":
        default = {"cancellable": False, "fee_inr": None, "reason": "DELIVERED orders cannot be cancelled."}
    elif picked_up:
        default = {"cancellable": False, "fee_inr": None,
                   "reason": "Order is PICKED_UP; use return-to-origin, do not cancel."}
    else:  # BOOKED, not picked up
        if within_free_window:
            default = {"cancellable": True, "fee_inr": 0,
                       "reason": f"Within {FREE_CANCEL_WINDOW_MIN} min of booking: no fee."}
        else:
            default = {"cancellable": True, "fee_inr": DEFAULT_CANCEL_FEE_INR,
                       "reason": f"After {FREE_CANCEL_WINDOW_MIN} min: INR {DEFAULT_CANCEL_FEE_INR} "
                                 f"unless the customer agreement waives it."}

    return {
        "order_id": order_id,
        "account_id": order.get("account_id"),
        "status": status,
        "picked_up": picked_up,
        "booked_at": order.get("booked_at"),
        "cancellation_requested_at": order.get("cancellation_requested_at"),
        "minutes_from_booking_to_request": mins_booking_to_request,
        "free_cancel_window_min": FREE_CANCEL_WINDOW_MIN,
        "within_free_window": within_free_window,
        "default_sop_outcome": default,
        "has_customer_agreement": has_contract,
        "contract_file": acct.get("contract_file"),
        "contract_may_override": has_contract and default.get("fee_inr") not in (0, None),
        "note": "If contract_may_override is true, read the customer agreement before finalising.",
    }


def service_credit_check(ctx: AuthContext, order_id: str) -> dict:
    """Compute failed-pickup service-credit facts + the DEFAULT SOP outcome."""
    order = get_order(ctx, order_id)
    if "error" in order:
        return order

    window_end = parse_dt(order.get("pickup_window_end"))
    actual = parse_dt(order.get("pickup_actual_at"))
    # If not yet picked up, measure lateness against the snapshot "now".
    reference = actual or config.now()
    delay_min = minutes_between(reference, window_end)
    carrier_fault = to_bool(order.get("carrier_fault"))
    customer_fault = to_bool(order.get("customer_fault"))

    try:
        fee = float(order.get("shipment_fee_inr")) if order.get("shipment_fee_inr") else None
    except (TypeError, ValueError):
        fee = None

    default_credit = None
    if fee is not None:
        default_credit = round(min(DEFAULT_CREDIT_CAP_INR, DEFAULT_CREDIT_RATE * fee), 2)

    eligible_default = bool(
        delay_min is not None and delay_min > DEFAULT_CREDIT_DELAY_MIN
        and carrier_fault and not customer_fault
    )

    acct = _fetch_account(order.get("account_id")) or {}
    has_contract = bool(acct.get("contract_file"))

    return {
        "order_id": order_id,
        "account_id": order.get("account_id"),
        "pickup_window_end": order.get("pickup_window_end"),
        "pickup_actual_at": order.get("pickup_actual_at"),
        "picked_up": actual is not None,
        "measured_against": "actual pickup" if actual else "dataset snapshot (still not picked up)",
        "delay_minutes_past_window": delay_min,
        "carrier_fault": carrier_fault,
        "customer_fault": customer_fault,
        "shipment_fee_inr": fee,
        "default_sop": {
            "delay_threshold_min": DEFAULT_CREDIT_DELAY_MIN,
            "eligible": eligible_default,
            "credit_inr": default_credit if eligible_default else 0,
            "formula": "lower of INR 500 or 10% of shipment fee",
        },
        "has_customer_agreement": has_contract,
        "contract_file": acct.get("contract_file"),
        "contract_may_override": has_contract,
        "manager_approval_required_above_inr": MANAGER_APPROVAL_ABOVE_INR,
        "note": "A signed agreement may change the delay threshold, amount, or cap. "
                "Read the customer agreement when contract_may_override is true.",
    }
