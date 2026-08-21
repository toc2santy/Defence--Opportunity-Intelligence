"""
Tests for capability taxonomy management. The most important test
here isn't "does the happy path work" — it's proving the actual
security boundary holds: a normal tenant admin (which is EVERY
signed-up user, by design) must NOT be able to write to shared,
global taxonomy data that every other tenant's classifier depends
on. That's the real bug this feature was built specifically to
avoid.
"""

import uuid


def test_list_taxonomy_is_readable_by_any_authenticated_user(client, auth_headers):
    resp = client.get("/admin/taxonomy", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) > 0  # the Phase 1/3 seed data should already be present
    assert "code" in body[0]
    assert "keyword_count" in body[0]


def test_taxonomy_list_requires_authentication(client):
    resp = client.get("/admin/taxonomy")
    assert resp.status_code in (401, 403)


def test_regular_tenant_admin_cannot_create_taxonomy_entry(client, auth_headers):
    """
    THE critical test. auth_headers belongs to a freshly signed-up
    user, who — by this system's own design — IS a tenant 'admin'.
    If this returns anything other than 403, the security boundary
    this whole feature exists to enforce is broken.
    """
    resp = client.post(
        "/admin/taxonomy",
        headers=auth_headers,
        json={"code": "SHOULD.NOT.WORK", "label": "Should Not Work", "sector": "Test"},
    )
    assert resp.status_code == 403


def test_regular_tenant_admin_cannot_add_taxonomy_keyword(client, auth_headers):
    # get a real existing capability to try (mis)using
    list_resp = client.get("/admin/taxonomy", headers=auth_headers)
    capability_id = list_resp.json()[0]["id"]

    resp = client.post(
        f"/admin/taxonomy/{capability_id}/keywords",
        headers=auth_headers,
        json={"keyword": "should-not-work", "weight": 3},
    )
    assert resp.status_code == 403


def test_regular_tenant_admin_cannot_remove_taxonomy_keyword(client, auth_headers):
    resp = client.delete(
        "/admin/taxonomy/keywords/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert resp.status_code == 403  # rejected before it even checks whether the ID exists


def test_platform_admin_can_create_taxonomy_entry_and_add_keywords(client, auth_headers, db_cursor):
    # Grant platform admin directly via DB — this is deliberately
    # NOT reachable through any API route, matching the real
    # bootstrap process (a manual database update by whoever
    # operates the platform, never a self-service signup path).
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )

    unique_code = f"TEST.{uuid.uuid4().hex[:8].upper()}"
    create_resp = client.post(
        "/admin/taxonomy",
        headers=auth_headers,
        json={"code": unique_code, "label": "Test Capability", "sector": "Test Sector"},
    )
    assert create_resp.status_code == 201
    capability_id = create_resp.json()["id"]

    kw_resp = client.post(
        f"/admin/taxonomy/{capability_id}/keywords",
        headers=auth_headers,
        json={"keyword": "testkeyword123", "weight": 3},
    )
    assert kw_resp.status_code == 201
    keyword_id = kw_resp.json()["id"]

    list_resp = client.get(f"/admin/taxonomy/{capability_id}/keywords", headers=auth_headers)
    assert any(k["id"] == keyword_id for k in list_resp.json())

    # and remove it cleanly
    del_resp = client.delete(f"/admin/taxonomy/keywords/{keyword_id}", headers=auth_headers)
    assert del_resp.status_code == 200


def test_duplicate_taxonomy_code_is_rejected(client, auth_headers, db_cursor):
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )
    unique_code = f"TEST.DUP.{uuid.uuid4().hex[:8].upper()}"
    first = client.post(
        "/admin/taxonomy", headers=auth_headers,
        json={"code": unique_code, "label": "First", "sector": "Test"},
    )
    assert first.status_code == 201

    second = client.post(
        "/admin/taxonomy", headers=auth_headers,
        json={"code": unique_code, "label": "Second, Same Code", "sector": "Test"},
    )
    assert second.status_code == 409


def _extract_user_id_from_token(headers):
    """Decode the JWT (without verifying — test-only convenience) to get the sub claim."""
    import base64
    import json

    token = headers["Authorization"].split(" ")[1]
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    return payload["sub"]
