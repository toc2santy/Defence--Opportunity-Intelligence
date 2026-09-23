"""
Pure unit tests for app/mfa.py — no DB, no network, no FastAPI.
Uses a real Fernet key generated at import time (not a fixed literal
committed to the repo), and real pyotp TOTP generation/verification —
these are the actual RFC 6238 primitives, not mocked.
"""

import time

import pyotp
import pytest
from cryptography.fernet import Fernet

import app.mfa as mfa


@pytest.fixture(autouse=True)
def _real_encryption_key(monkeypatch):
    monkeypatch.setattr(mfa, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_generate_totp_secret_is_a_valid_base32_secret_pyotp_accepts():
    secret = mfa.generate_totp_secret()
    # Constructing a TOTP object from it and generating a code must
    # not raise — the real proof a base32 secret is well-formed.
    code = pyotp.TOTP(secret).now()
    assert len(code) == 6
    assert code.isdigit()


def test_encrypt_then_decrypt_round_trips_the_real_secret():
    secret = mfa.generate_totp_secret()
    encrypted = mfa.encrypt_secret(secret)
    assert encrypted != secret  # actually encrypted, not a no-op
    assert mfa.decrypt_secret(encrypted) == secret


def test_encrypt_raises_without_a_configured_key(monkeypatch):
    monkeypatch.setattr(mfa, "MFA_ENCRYPTION_KEY", None)
    with pytest.raises(mfa.MfaConfigError):
        mfa.encrypt_secret("anything")


def test_decrypt_raises_on_corrupted_or_foreign_ciphertext():
    other_key_encrypted = Fernet(Fernet.generate_key()).encrypt(b"secret").decode()
    with pytest.raises(mfa.MfaConfigError):
        mfa.decrypt_secret(other_key_encrypted)


def test_provisioning_uri_carries_the_real_issuer_and_email():
    secret = mfa.generate_totp_secret()
    uri = mfa.provisioning_uri(secret, "admin@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "admin%40example.com" in uri or "admin@example.com" in uri
    assert "Defence%20Opportunity%20Intelligence" in uri or "Defence Opportunity Intelligence" in uri


def test_verify_totp_accepts_the_real_current_code():
    secret = mfa.generate_totp_secret()
    current_code = pyotp.TOTP(secret).now()
    assert mfa.verify_totp(secret, current_code) is True


def test_verify_totp_rejects_a_wrong_code():
    secret = mfa.generate_totp_secret()
    assert mfa.verify_totp(secret, "000000") is False


def test_verify_totp_rejects_empty_or_missing_code():
    secret = mfa.generate_totp_secret()
    assert mfa.verify_totp(secret, "") is False
    assert mfa.verify_totp(secret, None) is False


def test_verify_totp_rejects_a_code_from_a_different_secret():
    secret_a = mfa.generate_totp_secret()
    secret_b = mfa.generate_totp_secret()
    code_from_b = pyotp.TOTP(secret_b).now()
    assert mfa.verify_totp(secret_a, code_from_b) is False


def test_verify_totp_tolerates_one_step_of_clock_drift():
    """valid_window=1 — a code from 30s ago must still verify, real clock skew is common."""
    secret = mfa.generate_totp_secret()
    totp = pyotp.TOTP(secret)
    thirty_seconds_ago = int(time.time()) - 30
    drifted_code = totp.at(thirty_seconds_ago)
    assert mfa.verify_totp(secret, drifted_code) is True


def test_generate_backup_codes_returns_the_right_count_and_shape():
    codes = mfa.generate_backup_codes()
    assert len(codes) == mfa.BACKUP_CODE_COUNT
    assert len(set(codes)) == len(codes)  # no real duplicates
    for code in codes:
        groups = code.split("-")
        assert len(groups) == mfa.BACKUP_CODE_GROUPS
        for g in groups:
            assert len(g) == mfa.BACKUP_CODE_GROUP_LEN
            assert all(c in mfa.BACKUP_CODE_ALPHABET for c in g)


def test_backup_code_alphabet_excludes_visually_ambiguous_characters():
    for ambiguous in "01OIL":
        assert ambiguous not in mfa.BACKUP_CODE_ALPHABET


def test_hash_backup_code_is_deterministic_and_case_insensitive():
    code = "ABCD-2345-EFGH-6789-JKMN"
    assert mfa.hash_backup_code(code) == mfa.hash_backup_code(code)
    assert mfa.hash_backup_code(code) == mfa.hash_backup_code(code.lower())
    assert mfa.hash_backup_code(code) == mfa.hash_backup_code(f"  {code}  ")


def test_verify_backup_code_matches_a_real_generated_code():
    codes = mfa.generate_backup_codes()
    hashed = [mfa.hash_backup_code(c) for c in codes]
    assert mfa.verify_backup_code(codes[3], hashed) is True


def test_verify_backup_code_rejects_an_unknown_code():
    codes = mfa.generate_backup_codes()
    hashed = [mfa.hash_backup_code(c) for c in codes]
    assert mfa.verify_backup_code("ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ", hashed) is False


def test_verify_backup_code_rejects_empty_input():
    assert mfa.verify_backup_code("", ["somehash"]) is False
    assert mfa.verify_backup_code("REAL-CODE", []) is False
    assert mfa.verify_backup_code("REAL-CODE", None) is False


def test_a_used_backup_code_removed_from_the_list_no_longer_verifies():
    """
    Pins the actual "remove on use" contract the login route relies
    on: once a code's hash is filtered out of the stored array, the
    same code must never verify again (replay prevention).
    """
    codes = mfa.generate_backup_codes()
    hashed = [mfa.hash_backup_code(c) for c in codes]
    used_code = codes[0]
    remaining = [h for h in hashed if h != mfa.hash_backup_code(used_code)]
    assert mfa.verify_backup_code(used_code, remaining) is False
    assert mfa.verify_backup_code(codes[1], remaining) is True
