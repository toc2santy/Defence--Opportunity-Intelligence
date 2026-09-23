"""
MFA (TOTP, RFC 6238) — pure logic + at-rest encryption helpers. No
FastAPI, no DB — same separation as every normalize.py module in this
project, so this is directly unit-testable and the route handlers in
main.py stay thin orchestration.

Second security-controls item after Eligibility/Export-Control Phase
1 (see CLAUDE.md, 2026-09): login today is password-only, so one
compromised password gives full access to a tenant's tender/pricing
data with nothing else in the way.

mfa_secret is encrypted at rest with a DEDICATED key
(MFA_ENCRYPTION_KEY), deliberately separate from BACKUP_ENCRYPTION_KEY
(app/backup.py) — reusing one secret for two unrelated purposes means
a leak of either compromises both; keeping them independent keeps the
blast radius of each contained to what it actually protects.

Backup codes are generated in PLAINTEXT once (shown to the user
exactly one time, same as a password-reset token's raw value), and
only their SHA-256 hashes are ever persisted — the same "hash, never
the secret" rule password_hash and password_reset_tokens.token_hash
already follow in this schema.
"""

import hashlib
import os
import secrets

import pyotp
from cryptography.fernet import Fernet, InvalidToken

MFA_ISSUER = "Defence Opportunity Intelligence"
BACKUP_CODE_COUNT = 10
# 5 groups of 4 alphanumeric chars ("xxxx-xxxx-xxxx-xxxx-xxxx") —
# long enough to make guessing impractical, short enough to
# plausibly type by hand if a user's authenticator app is
# unavailable and they're reading the code off a printed copy.
BACKUP_CODE_GROUPS = 5
BACKUP_CODE_GROUP_LEN = 4
# Excludes visually ambiguous characters (0/O, 1/I/L) — a backup code
# is meant to be read and typed by a human under stress (locked out
# of their account), not just machine-generated.
BACKUP_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

MFA_ENCRYPTION_KEY = os.environ.get("MFA_ENCRYPTION_KEY")


class MfaConfigError(Exception):
    """Raised when MFA_ENCRYPTION_KEY is missing — never silently fall back to plaintext."""


def _fernet() -> Fernet:
    if not MFA_ENCRYPTION_KEY:
        raise MfaConfigError(
            "MFA_ENCRYPTION_KEY is not set — generate one with "
            "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "and add it to .env before enabling MFA"
        )
    return Fernet(MFA_ENCRYPTION_KEY.encode())


def generate_totp_secret() -> str:
    """A fresh base32 TOTP secret — pyotp's own generator (RFC 4648 base32, 160 bits)."""
    return pyotp.random_base32()


def encrypt_secret(plain_secret: str) -> str:
    return _fernet().encrypt(plain_secret.encode()).decode()


def decrypt_secret(encrypted_secret: str) -> str:
    try:
        return _fernet().decrypt(encrypted_secret.encode()).decode()
    except InvalidToken as e:
        # Only reachable if MFA_ENCRYPTION_KEY was rotated without a
        # re-enrollment migration, or the stored value was corrupted —
        # never silently treat an undecryptable secret as "no MFA".
        raise MfaConfigError("Stored MFA secret could not be decrypted — MFA_ENCRYPTION_KEY may have changed") from e


def provisioning_uri(secret: str, email: str) -> str:
    """The otpauth:// URI an authenticator app scans/imports — standard TOTP, 6 digits, 30s step (pyotp defaults, matching every major authenticator app)."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=MFA_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    """
    valid_window=1 accepts the previous/next 30s step too — real
    clock drift between a phone and this server is common enough that
    a zero-tolerance window would generate real support tickets over
    a few seconds of skew, at the cost of only slightly widening the
    brute-force window (still bounded by the login rate limiter, the
    actual defense against guessing).
    """
    code = (code or "").strip()
    if not code:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    return [
        "-".join(
            "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(BACKUP_CODE_GROUP_LEN))
            for _ in range(BACKUP_CODE_GROUPS)
        )
        for _ in range(count)
    ]


def hash_backup_code(code: str) -> str:
    # Normalized (upper, no surrounding whitespace) before hashing so
    # a user retyping a code with different casing or stray spaces
    # still matches — the alphabet itself is already uppercase-only,
    # this only guards against how a human actually types it back in.
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def verify_backup_code(code: str, hashed_codes: list[str]) -> bool:
    if not code or not hashed_codes:
        return False
    return hash_backup_code(code) in hashed_codes
