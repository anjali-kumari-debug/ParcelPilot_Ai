"""Mocked authentication + the AuthContext that powers access control.

In a real system these identities would come from a login/SSO and a signed token.
For the assessment we mock a small set of identities. The important part is NOT
the login - it is that every tool receives an `AuthContext` and enforces scoping
in code (see D3 in docs/DECISIONS.md). The model can never widen its own access.
"""

from __future__ import annotations

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
