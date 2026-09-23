"""
Integration tests for GET /intelligence/competitors. Hits the live
API over HTTP.

A fresh tenant has confirmed zero capabilities by construction, so
the "no confirmed capabilities" branch is what a real new signup
actually exercises — no seeding needed to test it.
"""

import uuid


def test_competitors_requires_auth(client):
    resp = client.get("/intelligence/competitors")
    assert resp.status_code in (401, 403)


def test_fresh_tenant_has_no_confirmed_capabilities(client, auth_headers):
    resp = client.get("/intelligence/competitors", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_confirmed_capabilities"] is False
    assert body["competitors"] == []


def test_competitor_surfaces_when_capability_is_confirmed(client, auth_headers, db_cursor):
    """
    Seeds a controlled programme + winner + contract_award directly
    (same pattern test_phase3_matching.py uses), confirms
    NAVAL.SYSTEMS for a real product via the actual API, and checks
    the seeded winner appears as a competitor — NAVAL.SYSTEMS has a
    curated CPV mapping (see db/migrations/010) so '35500000'
    resolves deterministically.
    """
    db_cursor.execute("select id from sources where name = 'EU TED (Tenders Electronic Daily)'")
    source_id = db_cursor.fetchone()["id"]
    db_cursor.execute("select id from capability_taxonomy where code = 'NAVAL.SYSTEMS'")
    capability_id = db_cursor.fetchone()["id"]

    fixture_ref = f"test-competitor-fixture-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Warship Hull Maintenance", "DEU", "contract_awarded", source_id, "35500000", fixture_ref),
    )
    programme_id = db_cursor.fetchone()["id"]

    winner_name = f"Test Competitor Shipyard {uuid.uuid4().hex[:8]}"
    db_cursor.execute(
        """
        insert into organizations (name, org_type, country, classification)
        values (%s, 'oem', 'DEU', 'public')
        returning id
        """,
        (winner_name,),
    )
    winner_org_id = db_cursor.fetchone()["id"]

    db_cursor.execute(
        """
        insert into contract_awards (programme_id, winner_organization_id, source_id)
        values (%s, %s, %s)
        """,
        (programme_id, winner_org_id, source_id),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Hull Coating System", "description": "Naval hull maintenance", "trl": 7},
    )
    product_id = create_resp.json()["id"]

    db_cursor.execute(
        "insert into product_capabilities (product_id, capability_id, classified_by) values (%s, %s, 'analyst')",
        (product_id, capability_id),
    )

    resp = client.get("/intelligence/competitors", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_confirmed_capabilities"] is True
    names = [c["organization_name"] for c in body["competitors"]]
    assert winner_name in names
