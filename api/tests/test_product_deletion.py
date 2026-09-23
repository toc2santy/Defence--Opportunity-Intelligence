"""
Tests for DELETE /products/{id} — a real correction mechanism for
wrong data entry, and the cascade fix that makes it actually work
when a product has existing matched opportunities.
"""

import uuid


def test_delete_product_removes_it_from_the_list(client, auth_headers):
    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Product To Delete", "description": "test", "trl": 5},
    )
    product_id = create_resp.json()["id"]

    list_before = client.get("/products", headers=auth_headers)
    assert any(p["id"] == product_id for p in list_before.json())

    delete_resp = client.delete(f"/products/{product_id}", headers=auth_headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "deleted"

    list_after = client.get("/products", headers=auth_headers)
    assert not any(p["id"] == product_id for p in list_after.json())


def test_delete_requires_authentication(client):
    resp = client.delete("/products/00000000-0000-0000-0000-000000000000")
    assert resp.status_code in (401, 403)


def test_delete_nonexistent_product_returns_404(client, auth_headers):
    resp = client.delete(
        "/products/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert resp.status_code == 404


def test_tenant_cannot_delete_another_tenants_product(client, auth_headers):
    # tenant A creates a real product
    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Tenant A's Product", "description": "test", "trl": 5},
    )
    product_id = create_resp.json()["id"]

    # tenant B tries to delete it by its real ID
    email_b = f"delete-test-b-{uuid.uuid4().hex[:8]}@example.com"
    signup_b = client.post(
        "/auth/signup",
        json={"company_name": "Rival Corp", "full_name": "Test Admin", "email": email_b, "password": "another-real-password-1"},
    )
    token_b = signup_b.json()["access_token"]

    delete_attempt = client.delete(
        f"/products/{product_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    # RLS means the DELETE matches zero rows for tenant B — same
    # 404 as a genuinely nonexistent ID, not a 403. That's correct:
    # it shouldn't even reveal that a product with this ID exists
    # in someone else's account.
    assert delete_attempt.status_code == 404

    # and tenant A's product must still be there, completely unaffected
    list_a = client.get("/products", headers=auth_headers)
    assert any(p["id"] == product_id for p in list_a.json())


def test_delete_product_with_existing_matched_opportunity_does_not_error(client, auth_headers, db_cursor):
    """
    THE real regression test for the cascade fix. Before
    011_product_deletion_cascade.sql, this exact sequence would
    have failed with a foreign key violation instead of a clean
    200 — a product with a real matched opportunity genuinely could
    not be deleted at all.
    """
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-delete-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Delete Cascade UAV Programme", "United States", "rfp_issued", source_id, "336411", fixture_ref),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Product With A Real Match", "description": "UAV drone system", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    uav_candidate = next(c for c in classify_resp.json()["candidates"] if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm", headers=auth_headers)
    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    assert len(match_resp.json()["matches"]) > 0, "test requires a real matched opportunity to exist before deleting"

    # this is the actual thing under test — must succeed, not 500
    delete_resp = client.delete(f"/products/{product_id}", headers=auth_headers)
    assert delete_resp.status_code == 200

    list_after = client.get("/products", headers=auth_headers)
    assert not any(p["id"] == product_id for p in list_after.json())
