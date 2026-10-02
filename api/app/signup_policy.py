"""
Invite-only sign-up policy (2026-10) — pure logic, no I/O, so it is
unit-testable without a server (same split as app/mfa.py).

The allowlist is a comma-separated string of exact emails and/or
"@domain.com" entries. "*" means open sign-up. This gates the CREATION
of new tenants only (password sign-up and the SSO new-account path);
login and SSO auto-link for an existing user never consult it.
"""


def parse_allowlist(raw: str) -> list[str]:
    return [e.strip().lower() for e in (raw or "*").split(",") if e.strip()]


def is_allowed(email: str, allowlist: list[str]) -> bool:
    if "*" in allowlist:
        return True
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    domain = "@" + email.rsplit("@", 1)[1]
    return email in allowlist or domain in allowlist
