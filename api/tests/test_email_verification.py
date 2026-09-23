"""
Integration tests for email verification on signup (2026-09, migration
048): POST /auth/verify-email, POST /auth/resend-verification, and the
email_verified field on /auth/signup + /auth/me.

Unlike test_password_reset.py, the accept-path (a token that actually
verifies) IS testable here purely at the HTTP boundary: db_cursor seeds
an email_verification_tokens row with a hash we compute ourselves —
exactly what /auth/signup's own INSERT does — then the token is
submitted through the real /auth/verify-email endpoint, same as a real
click would. The raw token itself is still never read back from the
DB (only its SHA-256 is ever stored), so this proves the real code
path, not a shortcut around it.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def _seed_verification_token(db_cursor, user_id, *, expired=False, used=False):
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + (timedelta(minutes=-5) if expired else timedelta(hours=24))
    used_at = datetime.now(timezone.utc) if used else None
    db_cursor.execute(
        """
        insert into email_verification_tokens (user_id, token_hash, expires_at, used_at)
        values (%s, %s, %s, %s)
        """,
        (user_id, token_hash, expires_at, used_at),
    )
    return raw_token


def _user_id_for_email(db_cursor, email):
    db_cursor.execute("select id from users where email = %s", (email,))
    return db_cursor.fetchone()["id"]


def test_new_signup_is_unverified(client, new_tenant):
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {new_tenant['token']}"})
    assert resp.status_code == 200
    assert resp.json()["email_verified"] is False


def test_verify_email_with_a_real_token_marks_the_account_verified(client, new_tenant, db_cursor):
    user_id = _user_id_for_email(db_cursor, new_tenant["email"])
    raw_token = _seed_verification_token(db_cursor, user_id)

    resp = client.post("/auth/verify-email", json={"token": raw_token})
    assert resp.status_code == 200

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {new_tenant['token']}"})
    assert me.json()["email_verified"] is True


def test_verify_email_rejects_unknown_token(client):
    resp = client.post("/auth/verify-email", json={"token": "not-a-real-token"})
    assert resp.status_code == 400


def test_verify_email_rejects_expired_token(client, new_tenant, db_cursor):
    user_id = _user_id_for_email(db_cursor, new_tenant["email"])
    raw_token = _seed_verification_token(db_cursor, user_id, expired=True)

    resp = client.post("/auth/verify-email", json={"token": raw_token})
    assert resp.status_code == 400


def test_verify_email_token_cannot_be_reused(client, new_tenant, db_cursor):
    user_id = _user_id_for_email(db_cursor, new_tenant["email"])
    raw_token = _seed_verification_token(db_cursor, user_id)

    first = client.post("/auth/verify-email", json={"token": raw_token})
    assert first.status_code == 200

    second = client.post("/auth/verify-email", json={"token": raw_token})
    assert second.status_code == 400


def test_resend_verification_sends_a_new_token_for_an_unverified_account(client, new_tenant, db_cursor):
    resp = client.post("/auth/resend-verification", headers={"Authorization": f"Bearer {new_tenant['token']}"})
    assert resp.status_code == 200
    assert "sent" in resp.json()["message"].lower()

    db_cursor.execute(
        "select count(*) as n from email_verification_tokens where user_id = %s",
        (_user_id_for_email(db_cursor, new_tenant["email"]),),
    )
    assert db_cursor.fetchone()["n"] >= 1


def test_resend_verification_is_a_no_op_once_already_verified(client, new_tenant, db_cursor):
    user_id = _user_id_for_email(db_cursor, new_tenant["email"])
    raw_token = _seed_verification_token(db_cursor, user_id)
    client.post("/auth/verify-email", json={"token": raw_token})

    resp = client.post("/auth/resend-verification", headers={"Authorization": f"Bearer {new_tenant['token']}"})
    assert resp.status_code == 200
    assert "already verified" in resp.json()["message"].lower()


def test_resend_verification_requires_auth(client):
    resp = client.post("/auth/resend-verification")
    # 403, not 401 — FastAPI's HTTPBearer rejects a MISSING credential
    # with 403 (no WWW-Authenticate challenge to issue); 401 is
    # reserved for a token that was present but invalid/expired,
    # same distinction every other auth-required route in this app
    # already makes.
    assert resp.status_code == 403
