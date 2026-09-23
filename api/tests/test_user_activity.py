"""
Integration tests for the platform-admin per-user activity log
(2026-09) — `audit_log` has been written to since Phase 0 across 23
call sites (signups, password/MFA changes, product/opportunity
actions, every platform-admin action) but had no route to read it
back until now.
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


def test_activity_requires_platform_admin_role(client, auth_headers):
    own_id = _extract_user_id_from_token(auth_headers)
    resp = client.get(f"/platform-admin/users/{own_id}/activity", headers=auth_headers)
    assert resp.status_code == 403


def test_activity_unknown_user_returns_404(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    resp = client.get(f"/platform-admin/users/{uuid.uuid4()}/activity", headers=auth_headers)
    assert resp.status_code == 404


def test_activity_shows_a_real_signup_entry_for_a_fresh_account(client, auth_headers, db_cursor):
    """
    A fresh signup itself writes a 'tenant.created_via_signup' entry
    (see the /auth/signup route) — the simplest real, guaranteed-to-
    exist activity row to assert against without seeding anything by
    hand.
    """
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"activity-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Activity Co {uuid.uuid4().hex[:6]}", "full_name": "Activity Admin",
              "email": target_email, "password": "a-real-password-123"},
    )
    assert signup.status_code == 201
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {signup.json()['access_token']}"})

    resp = client.get(f"/platform-admin/users/{target_id}/activity", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == target_email
    assert any(e["action"] == "tenant.created_via_signup" for e in body["entries"])


def test_activity_reflects_a_real_password_change(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"activity-{uuid.uuid4().hex[:8]}@example.com"
    target_password = "a-real-password-123"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Activity Co {uuid.uuid4().hex[:6]}", "full_name": "Activity Admin",
              "email": target_email, "password": target_password},
    )
    target_token = signup.json()["access_token"]
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {target_token}"})
    target_headers = {"Authorization": f"Bearer {target_token}"}

    client.post(
        "/auth/change-password", headers=target_headers,
        json={"current_password": target_password, "new_password": "a-different-password-1"},
    )

    resp = client.get(f"/platform-admin/users/{target_id}/activity", headers=auth_headers)
    assert resp.status_code == 200
    assert any(e["action"] == "user.password_changed" for e in resp.json()["entries"])


def test_activity_only_shows_this_users_own_entries_not_another_tenants(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    email_a = f"activity-a-{uuid.uuid4().hex[:8]}@example.com"
    email_b = f"activity-b-{uuid.uuid4().hex[:8]}@example.com"
    token_a = client.post(
        "/auth/signup",
        json={"company_name": f"A Co {uuid.uuid4().hex[:6]}", "full_name": "A", "email": email_a, "password": "a-real-password-123"},
    ).json()["access_token"]
    client.post(
        "/auth/signup",
        json={"company_name": f"B Co {uuid.uuid4().hex[:6]}", "full_name": "B", "email": email_b, "password": "a-real-password-123"},
    )
    user_a_id = _extract_user_id_from_token({"Authorization": f"Bearer {token_a}"})

    resp = client.get(f"/platform-admin/users/{user_a_id}/activity", headers=auth_headers)
    assert resp.status_code == 200
    # A's own signup wrote exactly one 'tenant.created_via_signup' row
    # — if B's entries had leaked in, this would count two.
    signup_entries = [e for e in resp.json()["entries"] if e["action"] == "tenant.created_via_signup"]
    assert len(signup_entries) == 1
