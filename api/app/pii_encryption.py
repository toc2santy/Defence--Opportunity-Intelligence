"""
Encryption at rest for a tenant's own sensitive registration numbers
(GST/PAN/TAN/IEC — `tenants.gst_number`/`pan_number`/`tan_number`/
`iec_license`), 2026-09. Same Fernet-based pattern as app/mfa.py's
TOTP-secret encryption, and the same "a dedicated key per purpose"
discipline BACKUP_ENCRYPTION_KEY and MFA_ENCRYPTION_KEY already
established — reusing either of those here would mean a leak of one
secret compromises a completely unrelated one.

These four columns were plaintext in the database until now — a real
gap for fields that are, in effect, government-issued tax/trade
registration numbers, even though this platform never treats them as
authentication material the way a password is. Presence (NOT NULL)
still needs to work for the public compliance-badge check
(`(gst_number is not null) as has_gst`, see main.py) without
decrypting anything — Fernet ciphertext is still non-NULL, so that
check is unaffected and needed no change.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

PII_ENCRYPTION_KEY = os.environ.get("PII_ENCRYPTION_KEY")


class PiiConfigError(Exception):
    """Raised when PII_ENCRYPTION_KEY is missing — never silently fall back to plaintext."""


def _fernet() -> Fernet:
    if not PII_ENCRYPTION_KEY:
        raise PiiConfigError(
            "PII_ENCRYPTION_KEY is not set — generate one with "
            "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "and add it to .env before storing a GST/PAN/TAN/IEC number"
        )
    return Fernet(PII_ENCRYPTION_KEY.encode())


def encrypt_field(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_field(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        # Only reachable if PII_ENCRYPTION_KEY was rotated without a
        # re-encryption migration, or the stored value was corrupted —
        # never silently return an undecryptable value as if it were
        # real. Same "fail loud, not quiet" rule app/mfa.py's own
        # decrypt_secret already follows.
        raise PiiConfigError("A stored registration number could not be decrypted — PII_ENCRYPTION_KEY may have changed") from e
