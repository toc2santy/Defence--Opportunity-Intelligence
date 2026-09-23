"""
Integration tests for platform-admin block/unblock (2026-09) —
reuses `users.is_active`, already enforced at login, previously with
no route to ever flip it. Real use case: a tenant's service period
ending should suspend access without deleting their account or data.
"""

import uuid

import pytest


def _extract_user_id_from_token(headers):
    import base64
    import json

    token = headers["Authorization"].split(" ")[1]
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    return payload["sub"]


def _signup(client, email, password="a-real-password-123"):
    resp = client.post(
        "/auth/signup",
        json={"company_name": f"Blockable Co {uuid.uuid4().hex[:6]}", "full_name": "Blockable Admin",
              "email": email, "password": password},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _grant_platform_admin(db_cursor, auth_headers):
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )


def test_block_requires_platform_admin_role(client, auth_headers):
    target_email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    token = _signup(client, target_email)
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {token}"})

    resp = client.post(f"/platform-admin/users/{target_id}/block", headers=auth_headers)
    assert resp.status_code == 403


def test_block_a_real_user_prevents_login_and_unblock_restores_it(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    target_password = "a-real-password-123"
    token = _signup(client, target_email, target_password)
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {token}"})

    block_resp = client.post(f"/platform-admin/users/{target_id}/block", headers=auth_headers)
    assert block_resp.status_code == 200

    login_resp = client.post("/auth/login", json={"email": target_email, "password": target_password})
    assert login_resp.status_code == 401

    unblock_resp = client.post(f"/platform-admin/users/{target_id}/unblock", headers=auth_headers)
    assert unblock_resp.status_code == 200

    login_resp2 = client.post("/auth/login", json={"email": target_email, "password": target_password})
    assert login_resp2.status_code == 200
    assert login_resp2.json()["access_token"]


def test_block_an_already_blocked_user_returns_409(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    token = _signup(client, target_email)
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {token}"})

    assert client.post(f"/platform-admin/users/{target_id}/block", headers=auth_headers).status_code == 200
    second_block = client.post(f"/platform-admin/users/{target_id}/block", headers=auth_headers)
    assert second_block.status_code == 409


def test_unblock_a_not_blocked_user_returns_409(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    token = _signup(client, target_email)
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {token}"})

    resp = client.post(f"/platform-admin/users/{target_id}/unblock", headers=auth_headers)
    assert resp.status_code == 409


def test_admin_cannot_block_their_own_account(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    own_id = _extract_user_id_from_token(auth_headers)
    resp = client.post(f"/platform-admin/users/{own_id}/block", headers=auth_headers)
    assert resp.status_code == 400


def test_block_unknown_user_returns_404(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    resp = client.post(f"/platform-admin/users/{uuid.uuid4()}/block", headers=auth_headers)
    assert resp.status_code == 404


def test_blocked_status_visible_in_user_listing(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)
    target_email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    token = _signup(client, target_email)
    target_id = _extract_user_id_from_token({"Authorization": f"Bearer {token}"})
    client.post(f"/platform-admin/users/{target_id}/block", headers=auth_headers)

    resp = client.get("/platform-admin/users", headers=auth_headers, params={"search": target_email})
    assert resp.status_code == 200
    body = resp.json()
    assert body["users"][0]["is_active"] is False
