"""
Integration tests for session revocation (2026-09) — closes a real
gap: JWTs were fully stateless (30-minute expiry, no server-side
session store), so a password change, an admin blocking an account,
or an admin-triggered password reset all left the OLD token(s) fully
valid for up to 30 more minutes regardless. `users.session_version`,
embedded as the `sv` claim and checked on every request in
get_current_user, closes it — bumping the column instantly
invalidates every already-issued token for that user.
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


def test_a_normal_token_keeps_working_across_requests(client, auth_headers):
    """Sanity/non-regression: revocation must not break the ordinary case."""
    r1 = client.get("/auth/me", headers=auth_headers)
    r2 = client.get("/auth/me", headers=auth_headers)
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_change_password_revokes_the_calling_tokens_own_session(client, auth_headers, new_tenant):
    change_resp = client.post(
        "/auth/change-password", headers=auth_headers,
        json={"current_password": new_tenant["password"], "new_password": "a-new-real-password-1"},
    )
    assert change_resp.status_code == 200
    assert change_resp.json()["session_revoked"] is True

    # The SAME token that just succeeded must now be rejected.
    again = client.get("/auth/me", headers=auth_headers)
    assert again.status_code == 401


def test_a_fresh_login_after_password_change_gets_a_working_token(client, new_tenant):
    new_password = "a-new-real-password-1"
    client.post(
        "/auth/login",
        json={"email": new_tenant["email"], "password": new_tenant["password"]},
    )
    login1 = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_tenant["password"]})
    token1 = login1.json()["access_token"]
    headers1 = {"Authorization": f"Bearer {token1}"}

    client.post(
        "/auth/change-password", headers=headers1,
        json={"current_password": new_tenant["password"], "new_password": new_password},
    )

    login2 = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_password})
    assert login2.status_code == 200
    headers2 = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    me = client.get("/auth/me", headers=headers2)
    assert me.status_code == 200


def test_reset_password_via_link_revokes_the_old_session(client, auth_headers, new_tenant, db_cursor):
    """
    Uses the platform-admin-triggered reset link (already covered by
    test_password_management.py's own end-to-end chain) purely as a
    way to obtain a real, valid raw reset token without needing SMTP
    — the thing under test here is what /auth/reset-password itself
    does to session_version, not how the token was generated.
    """
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"revoke-target-{uuid.uuid4().hex[:8]}@example.com"
    target_password = "a-real-password-123"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Revoke Target Co {uuid.uuid4().hex[:6]}", "full_name": "Target",
              "email": target_email, "password": target_password},
    )
    target_token = signup.json()["access_token"]
    target_headers = {"Authorization": f"Bearer {target_token}"}
    assert client.get("/auth/me", headers=target_headers).status_code == 200

    trigger = client.post(
        "/platform-admin/users/reset-password", headers=auth_headers, json={"email": target_email}
    )
    raw_token = trigger.json()["reset_link"].split("reset_token=")[1]

    reset_resp = client.post(
        "/auth/reset-password", json={"token": raw_token, "new_password": "a-self-chosen-new-password-1"}
    )
    assert reset_resp.status_code == 200

    # The target's ORIGINAL token (issued before the reset) must now be dead.
    still_valid = client.get("/auth/me", headers=target_headers)
    assert still_valid.status_code == 401


def test_platform_admin_block_revokes_the_users_current_session_immediately(client, auth_headers, db_cursor):
    """
    The real point of this feature: blocking used to only stop FUTURE
    logins (an already-issued token kept working for up to 30 more
    minutes). It must now kill the CURRENT session on the user's very
    next request, not just their next login attempt.
    """
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"blockable-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Blockable Co {uuid.uuid4().hex[:6]}", "full_name": "Target",
              "email": target_email, "password": "a-real-password-123"},
    )
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {signup.json()['access_token']}"})
    target_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    assert client.get("/auth/me", headers=target_headers).status_code == 200

    block_resp = client.post(f"/platform-admin/users/{target_id}/block", headers=auth_headers)
    assert block_resp.status_code == 200

    still_valid = client.get("/auth/me", headers=target_headers)
    assert still_valid.status_code == 401


def test_logout_everywhere_revokes_the_calling_tokens_own_session(client, auth_headers):
    assert client.get("/auth/me", headers=auth_headers).status_code == 200
    resp = client.post("/auth/logout-everywhere", headers=auth_headers)
    assert resp.status_code == 200
    assert client.get("/auth/me", headers=auth_headers).status_code == 401


def test_logout_everywhere_does_not_affect_a_different_users_session(client, auth_headers, new_tenant, db_cursor):
    other_email = f"unaffected-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Unaffected Co {uuid.uuid4().hex[:6]}", "full_name": "Other",
              "email": other_email, "password": "a-real-password-123"},
    )
    other_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    client.post("/auth/logout-everywhere", headers=auth_headers)

    assert client.get("/auth/me", headers=other_headers).status_code == 200
