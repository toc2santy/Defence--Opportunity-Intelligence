"""
Integration tests for per-account failed-login lockout (2026-09) —
the complement to LOGIN_RATE_LIMIT (IP-keyed, already existed): this
locks the specific ACCOUNT after MAX_FAILED_LOGIN_ATTEMPTS (5) wrong
passwords in a row, independent of which IP the attempts came from.
"""

import uuid


def _extract_user_id_from_token(headers):
    import base64
    import json

    token = headers["Authorization"].split(" ")[1]
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    return payload["sub"]


def _grant_platform_admin(db_cursor, auth_headers):
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )


def test_five_wrong_passwords_lock_the_account(client, new_tenant):
    for _ in range(5):
        resp = client.post("/auth/login", json={"email": new_tenant["email"], "password": "totally-wrong-1"})
    assert resp.status_code == 423

    # The 6th attempt — even with the CORRECT password now — must
    # still be rejected while the lock is active. This is the real
    # point of a lockout: a leaked/guessed-right credential doesn't
    # help an attacker mid-window.
    still_locked = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_tenant["password"]})
    assert still_locked.status_code == 423


def test_fewer_than_five_wrong_attempts_does_not_lock(client, new_tenant):
    for _ in range(4):
        resp = client.post("/auth/login", json={"email": new_tenant["email"], "password": "totally-wrong-1"})
        assert resp.status_code == 401

    # A correct password on the 5th attempt must still succeed —
    # only 5 WRONG in a row locks, not "the 5th attempt regardless."
    good = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_tenant["password"]})
    assert good.status_code == 200


def test_a_correct_login_resets_the_failed_attempt_counter(client, new_tenant, db_cursor):
    for _ in range(3):
        client.post("/auth/login", json={"email": new_tenant["email"], "password": "totally-wrong-1"})

    good = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_tenant["password"]})
    assert good.status_code == 200

    db_cursor.execute("select failed_login_attempts from users where email = %s", (new_tenant["email"],))
    assert db_cursor.fetchone()["failed_login_attempts"] == 0

    # Confirms the counter really reset, not just cosmetically: 4
    # more wrong attempts (would total 7 if it hadn't reset, well
    # past the 5-attempt threshold) must NOT lock the account yet.
    for _ in range(4):
        resp = client.post("/auth/login", json={"email": new_tenant["email"], "password": "totally-wrong-1"})
    assert resp.status_code == 401


def test_platform_admin_can_unlock_a_locked_account(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"lockable-{uuid.uuid4().hex[:8]}@example.com"
    target_password = "a-real-password-123"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Lockable Co {uuid.uuid4().hex[:6]}", "full_name": "Lockable Admin",
              "email": target_email, "password": target_password},
    )
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {signup.json()['access_token']}"})

    for _ in range(5):
        client.post("/auth/login", json={"email": target_email, "password": "wrong-1"})
    locked = client.post("/auth/login", json={"email": target_email, "password": target_password})
    assert locked.status_code == 423

    unlock_resp = client.post(f"/platform-admin/users/{target_id}/unlock", headers=auth_headers)
    assert unlock_resp.status_code == 200

    good = client.post("/auth/login", json={"email": target_email, "password": target_password})
    assert good.status_code == 200


def test_unlock_requires_platform_admin_role(client, auth_headers):
    target_id = _extract_user_id_from_token(auth_headers)
    resp = client.post(f"/platform-admin/users/{target_id}/unlock", headers=auth_headers)
    assert resp.status_code == 403


def test_unlock_an_unlocked_account_returns_409(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    own_id = _extract_user_id_from_token(auth_headers)
    resp = client.post(f"/platform-admin/users/{own_id}/unlock", headers=auth_headers)
    assert resp.status_code == 409


def test_unlock_unknown_user_returns_404(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    resp = client.post(f"/platform-admin/users/{uuid.uuid4()}/unlock", headers=auth_headers)
    assert resp.status_code == 404


def test_a_nonexistent_email_still_returns_a_generic_401_not_a_423(client):
    """No account-enumeration signal from a lockout check on an email that was never registered."""
    resp = client.post("/auth/login", json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com", "password": "whatever12345"})
    assert resp.status_code == 401
