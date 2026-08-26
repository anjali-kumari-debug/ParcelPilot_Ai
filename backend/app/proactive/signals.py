"""Proactive issue detection (Problem 1) for authorised internal users.

Given the ticket/order/account tables, surface "what deserves attention":
  * SLA risk   - open tickets whose age exceeds (or is approaching) their target
                 first-response time, using plan defaults + contract overrides.
  * Clusters   - groups of similar tickets (semantic), highlighting repeated
                 product issues and problems that span multiple customers.
  * Severity   - inferred P1/P2/P3 from the ticket text.

Simplifying assumption (documented): "business hours" targets are treated as
clock minutes here. Good enough to rank risk for a demo; a production version
would use a real business-calendar.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..db import get_conn
from ..util import parse_dt, minutes_between
from ..embeddings import embed_texts
from .. import config

# First-response targets in minutes. Contract overrides take precedence.
_PLAN_TARGETS = {
    "Enterprise": {"P1": 30, "P2": 120, "P3": 480},
    "Growth": {"P1": 120, "P2": 240, "P3": 960},
    "Standard": {"P1": 240, "P2": 480, "P3": 960},
}
_CONTRACT_OVERRIDES = {
    "ACCT-001": {"P1": 15, "P2": 60, "P3": 480},   # Northstar agreement
    "ACCT-002": {"P1": 120, "P2": 240, "P3": 960}, # LumenWorks agreement
}

_P1_KEYWORDS = ("all ", "every", "outage", "500", "api key", "exposure",
                "security", "credential", "cannot create", "is failing")
_P2_KEYWORDS = ("fails", "degraded", "bulk", "partial", "slow", "unavailable")


def _infer_severity(subject: str, description: str) -> str:
    text = f"{subject} {description}".lower()
    if any(k in text for k in _P1_KEYWORDS):
        return "P1"
    if any(k in text for k in _P2_KEYWORDS):
        return "P2"
    return "P3"


def _target_minutes(account_id: str, plan: str | None, severity: str) -> int:
    if account_id in _CONTRACT_OVERRIDES:
        return _CONTRACT_OVERRIDES[account_id][severity]
    return _PLAN_TARGETS.get(plan or "Standard", _PLAN_TARGETS["Standard"])[severity]


def _load() -> tuple[list[dict], dict[str, dict]]:
    with get_conn() as conn:
        tickets = [dict(r) for r in conn.execute("SELECT * FROM tickets").fetchall()]
        accounts = {r["account_id"]: dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()}
    return tickets, accounts


def _sla_signals(tickets: list[dict], accounts: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    now = config.now()
    for t in tickets:
        if (t.get("status") or "").lower() != "open":
            continue
        acct = accounts.get(t["account_id"], {})
        severity = _infer_severity(t.get("subject", ""), t.get("description", ""))
        target = _target_minutes(t["account_id"], acct.get("plan"), severity)
        age = minutes_between(now, parse_dt(t.get("created_at")))
        if age is None:
            continue
        ratio = age / target if target else 0
        status = "breached" if age > target else ("at_risk" if ratio >= 0.75 else "ok")
        out.append({
            "ticket_id": t["ticket_id"],
            "account_id": t["account_id"],
            "account_name": acct.get("account_name"),
            "plan": acct.get("plan"),
            "severity": severity,
            "subject": t.get("subject"),
            "age_minutes": age,
            "target_minutes": target,
            "sla_status": status,
        })
    # Most urgent first: breached before at_risk, then by how far over target.
    order = {"breached": 0, "at_risk": 1, "ok": 2}
    out.sort(key=lambda s: (order[s["sla_status"]], -(s["age_minutes"] / s["target_minutes"])))
    return out


def _cluster(tickets: list[dict]) -> list[dict]:
    """Greedy semantic clustering of ticket text. Falls back to keyword overlap."""
    texts = [f"{t.get('subject','')} {t.get('description','')}" for t in tickets]
    try:
        vecs = np.array(embed_texts(texts), dtype=float)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = vecs / norms
        sim = unit @ unit.T
        threshold = 0.72
    except Exception:
        # Fallback: crude keyword-overlap similarity so the endpoint still works.
        sim = np.zeros((len(tickets), len(tickets)))
        tokens = [set(x.lower().split()) for x in texts]
        for i in range(len(tickets)):
            for j in range(len(tickets)):
                inter = len(tokens[i] & tokens[j])
                union = len(tokens[i] | tokens[j]) or 1
                sim[i][j] = inter / union
        threshold = 0.3

    assigned = [-1] * len(tickets)
    clusters: list[list[int]] = []
    for i in range(len(tickets)):
        if assigned[i] != -1:
            continue
        group = [i]
        assigned[i] = len(clusters)
        for j in range(i + 1, len(tickets)):
            if assigned[j] == -1 and sim[i][j] >= threshold:
                assigned[j] = len(clusters)
                group.append(j)
        clusters.append(group)

    result: list[dict] = []
    for group in clusters:
        if len(group) < 2:
            continue  # only report repeated issues
        members = [tickets[k] for k in group]
        accounts_in = sorted({m["account_id"] for m in members})
        result.append({
            "label": members[0].get("subject"),
            "size": len(members),
            "ticket_ids": [m["ticket_id"] for m in members],
            "accounts": accounts_in,
            "multi_customer": len(accounts_in) > 1,
        })
    result.sort(key=lambda c: -c["size"])
    return result


def detect_signals() -> dict[str, Any]:
    tickets, accounts = _load()
    sla = _sla_signals(tickets, accounts)
    clusters = _cluster(tickets)

    high_sev = [s for s in sla if s["severity"] == "P1"]
    breaches = [s for s in sla if s["sla_status"] == "breached"]

    # Accounts with more than one open ticket (possible concentrated problem).
    open_by_acct: dict[str, int] = {}
    for t in tickets:
        if (t.get("status") or "").lower() == "open":
            open_by_acct[t["account_id"]] = open_by_acct.get(t["account_id"], 0) + 1
    hotspots = [{"account_id": a, "open_tickets": n} for a, n in open_by_acct.items() if n > 1]

    return {
        "generated_at": config.now().isoformat(),
        "summary": {
            "open_tickets": sum(1 for t in tickets if (t.get("status") or "").lower() == "open"),
            "sla_breaches": len(breaches),
            "p1_tickets": len(high_sev),
            "clusters": len(clusters),
            "multi_customer_clusters": sum(1 for c in clusters if c["multi_customer"]),
        },
        "sla_risk": sla,
        "high_severity": high_sev,
        "clusters": clusters,
        "account_hotspots": hotspots,
    }
