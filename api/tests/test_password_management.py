"""
Integration tests for follow-ups requested alongside MFA (2026-09):
- POST /auth/change-password — logged-in self-service change, since
  this project's only prior way to change a password was the
  forgot/reset-password email flow, which needs real SMTP.
- GET /platform-admin/users + POST .../reset-password — a platform
  admin can search/select any tenant's user and trigger a reset LINK
  for them, but never sees or sets the actual new password — same
  security property as the public forgot-password route, just
  admin-triggered (and admin-discoverable) instead of self-serve.
"""

import uuid

import pytest


def _extract_user_id_from_token(headers):
    """Decode the JWT (without verifying — test-only convenience) to get the sub claim."""
    import base64
    import json

    token = headers["Authorization"].split(" ")[1]
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    return payload["sub"]


def test_change_password_wrong_current_password_fails(client, auth_headers):
    resp = client.post(
        "/auth/change-password", headers=auth_headers,
        json={"current_password": "totally-wrong-one", "new_password": "a-new-real-password-1"},
    )
    assert resp.status_code == 401


def test_change_password_rejects_a_short_new_password(client, auth_headers, new_tenant):
    resp = client.post(
        "/auth/change-password", headers=auth_headers,
        json={"current_password": new_tenant["password"], "new_password": "short"},
    )
    assert resp.status_code == 422


def test_change_password_succeeds_and_old_password_no_longer_works(client, auth_headers, new_tenant):
    new_password = "a-brand-new-real-password-1"
    resp = client.post(
        "/auth/change-password", headers=auth_headers,
        json={"current_password": new_tenant["password"], "new_password": new_password},
    )
    assert resp.status_code == 200

    old_login = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_tenant["password"]})
    assert old_login.status_code == 401

    new_login = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_password})
    assert new_login.status_code == 200
    assert new_login.json()["access_token"]


def test_platform_admin_reset_password_requires_platform_admin_role(client, auth_headers, new_tenant):
    resp = client.post(
        "/platform-admin/users/reset-password", headers=auth_headers,
        json={"email": new_tenant["email"]},
    )
    assert resp.status_code == 403


def test_platform_admin_reset_password_unknown_email_returns_404(client, auth_headers, db_cursor):
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )
    resp = client.post(
        "/platform-admin/users/reset-password", headers=auth_headers,
        json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert resp.status_code == 404


def test_platform_admin_can_trigger_a_working_reset_link_for_another_tenant(client, auth_headers, new_tenant, db_cursor):
    """
    The real end-to-end property that matters: the admin never sees
    or picks the target user's new password — only a one-time link
    that the SAME /auth/reset-password route (already used by the
    public forgot-password flow) accepts, letting the target user
    pick their own final password.
    """
    # A second, independent tenant — the "other tenant" the admin is
    # helping, distinct from auth_headers' own tenant.
    other_email = f"locked-out-{uuid.uuid4().hex[:8]}@example.com"
    other_password = "the-original-password-1"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Other Tenant {uuid.uuid4().hex[:6]}", "full_name": "Other Admin",
              "email": other_email, "password": other_password},
    )
    assert signup.status_code == 201

    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )

    trigger_resp = client.post(
        "/platform-admin/users/reset-password", headers=auth_headers,
        json={"email": other_email},
    )
    assert trigger_resp.status_code == 200
    body = trigger_resp.json()
    assert "reset_token=" in body["reset_link"]
    raw_token = body["reset_link"].split("reset_token=")[1]

    new_password = "a-self-chosen-new-password-1"
    reset_resp = client.post(
        "/auth/reset-password", json={"token": raw_token, "new_password": new_password}
    )
    assert reset_resp.status_code == 200

    old_login = client.post("/auth/login", json={"email": other_email, "password": other_password})
    assert old_login.status_code == 401
    new_login = client.post("/auth/login", json={"email": other_email, "password": new_password})
    assert new_login.status_code == 200


def test_list_all_users_requires_platform_admin_role(client, auth_headers):
    resp = client.get("/platform-admin/users", headers=auth_headers)
    assert resp.status_code == 403


def test_list_all_users_search_finds_a_specific_real_account(client, auth_headers, db_cursor):
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )
    target_email = f"findme-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/auth/signup",
        json={"company_name": f"Findable Co {uuid.uuid4().hex[:6]}", "full_name": "Findable Admin",
              "email": target_email, "password": "a-real-password-123"},
    )
    assert signup.status_code == 201

    resp = client.get("/platform-admin/users", headers=auth_headers, params={"search": target_email})
    assert resp.status_code == 200
    body = resp.json()
    assert body["returned"] == 1
    assert body["users"][0]["email"] == target_email


def test_list_all_users_default_excludes_pytest_fixture_tenants(client, auth_headers, db_cursor):
    """
    The default (no search) listing must not be drowned out by this
    suite's own throwaway "Test Company <hex>" tenants — every one of
    THOSE emails uses unique_email()'s "test-<hex>@example.com"
    shape, so none should appear in an unfiltered listing.
    """
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )
    resp = client.get("/platform-admin/users", headers=auth_headers)
    assert resp.status_code == 200
    for u in resp.json()["users"]:
        assert not u["tenant_name"].startswith("Test Company")
