"""Mocked authentication + the AuthContext that powers access control.

In a real system these identities would come from a login/SSO and a signed token.
For the assessment we mock a small set of identities. The important part is NOT
the login - it is that every tool receives an `AuthContext` and enforces scoping
in code (see D3 in docs/DECISIONS.md). The model can never widen its own access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Roles
ROLE_CUSTOMER = "customer"
ROLE_INTERNAL = "internal"


@dataclass(frozen=True)
class AuthContext:
    """Who is asking, and what they are allowed to touch.

    * role       - "customer" or "internal"
    * account_id - the customer's own account (None for internal staff)
    * user_name  - display/audit label
    """
    role: str
    account_id: str | None
    user_name: str

    @property
    def is_internal(self) -> bool:
        return self.role == ROLE_INTERNAL

    @property
    def is_customer(self) -> bool:
        return self.role == ROLE_CUSTOMER

    def scope_account(self, requested_account_id: str | None) -> str | None:
        """Resolve which account a tool may read.

        Customers are ALWAYS pinned to their own account - any account id the
        model tries to pass is ignored. Internal users may target a specific
        account or (when None) query across all accounts.
        """
        if self.is_customer:
            return self.account_id
        return requested_account_id  # internal: honour the request (may be None = all)

    def can_access_account(self, account_id: str | None) -> bool:
        if self.is_internal:
            return True
        return account_id is not None and account_id == self.account_id


# --- Mocked identity directory ---------------------------------------------
# Maps a login id -> identity. The UI "login" simply picks one of these.
MOCK_IDENTITIES: dict[str, AuthContext] = {
    # Customer logins (each pinned to one account)
    "northstar": AuthContext(ROLE_CUSTOMER, "ACCT-001", "Northstar Logistics"),
    "lumenworks": AuthContext(ROLE_CUSTOMER, "ACCT-002", "LumenWorks"),
    "beacon": AuthContext(ROLE_CUSTOMER, "ACCT-003", "Beacon Retail"),
    "axis": AuthContext(ROLE_CUSTOMER, "ACCT-004", "Axis Labs"),
    # Internal staff login (cross-account access)
    "ops": AuthContext(ROLE_INTERNAL, None, "ParcelPilot Ops"),
}


def get_identity(login_id: str) -> AuthContext | None:
    return MOCK_IDENTITIES.get(login_id)


def list_identities() -> list[dict]:
    """For the UI's login/role picker."""
    return [
        {"login_id": k, "role": v.role, "account_id": v.account_id, "user_name": v.user_name}
        for k, v in MOCK_IDENTITIES.items()
    ]


def _norm(s: str) -> str:
    """Lowercase and strip everything but letters/digits (so 'ACCT-002' -> 'acct002')."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _account_terms() -> dict[str, list[str]]:
    """Map each known account id -> distinctive normalized identifiers.

    We use the account id plus the brand (first) token of the display name, e.g.
    ACCT-002 -> ["acct002", "lumenworks"]. Generic descriptors like "logistics"
    or "retail" are intentionally excluded to avoid false matches.
    """
    terms: dict[str, list[str]] = {}
    for ctx in MOCK_IDENTITIES.values():
        if ctx.account_id is None:
            continue
        brand = ctx.user_name.split()[0] if ctx.user_name else ""
        toks = [t for t in (_norm(ctx.account_id), _norm(brand)) if t]
        terms.setdefault(ctx.account_id, [])
        for t in toks:
            if t not in terms[ctx.account_id]:
                terms[ctx.account_id].append(t)
    return terms


def _text_candidates(text: str) -> set[str]:
    """Whole words plus adjacent-word pairs, normalized.

    Matching against these (rather than raw substrings) makes the check robust to
    spacing/punctuation variants like 'Lumen Works' or 'ACCT 002' while avoiding
    mid-word false positives (e.g. 'axis' must not match inside 'taxis').
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    candidates: set[str] = set(words)
    for a, b in zip(words, words[1:]):
        candidates.add(a + b)
    return candidates


def find_foreign_account_refs(ctx: AuthContext, text: str) -> list[str]:
    """Return other accounts that a CUSTOMER explicitly referenced in `text`.

    Access control is enforced in the data/tool layer, but a customer asking
    about ANOTHER customer's account or contract must be refused outright rather
    than letting the model answer from its own (scoped) sources and mislabel it.
    Internal users may look across accounts, so this always returns empty for them.
    """
    if not ctx.is_customer or not text:
        return []
    candidates = _text_candidates(text)
    found: list[str] = []
    for acct_id, toks in _account_terms().items():
        if acct_id == ctx.account_id:
            continue
        if any(tok in candidates for tok in toks):
            found.append(acct_id)
    return found
