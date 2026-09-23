"""
Integration tests for GET /intelligence/partners.

These SEED the buyer overlap the feature depends on, because real
ingested data currently has almost none: contract awards come only
from TED/UK Find a Tender award notices, while most tenant
opportunities match SAM.gov/CanadaBuys programmes, so the same buyer
rarely appears on both sides yet. Seeding proves the logic is
correct independently of that data sparsity — the same reason
test_phase3_matching.py seeds its own programme fixture.
"""

import uuid


def test_partners_requires_auth(client):
    resp = client.get("/intelligence/partners")
    assert resp.status_code in (401, 403)


def test_fresh_tenant_has_no_confirmed_capabilities(client, auth_headers):
    body = client.get("/intelligence/partners", headers=auth_headers).json()
    assert body["has_confirmed_capabilities"] is False
    assert body["partners"] == []


def _seed_shared_buyer_scenario(client, auth_headers, db_cursor, partner_cpv, tenant_capability_code):
    """
    Builds the minimum real scenario: one buyer, two programmes from
    that buyer — one the tenant matches (their capability), one won by
    another company in a different capability.
    """
    db_cursor.execute("select id from sources where name = 'EU TED (Tenders Electronic Daily)'")
    source_id = db_cursor.fetchone()["id"]
    db_cursor.execute("select id from capability_taxonomy where code = %s", (tenant_capability_code,))
    capability_id = db_cursor.fetchone()["id"]

    buyer_name = f"Test Shared Buyer {uuid.uuid4().hex[:8]}"
    db_cursor.execute(
        "insert into organizations (name, org_type, country, classification) "
        "values (%s, 'government_body', 'DEU', 'public') returning id",
        (buyer_name,),
    )
    buyer_id = db_cursor.fetchone()["id"]

    # Programme A — the tenant's own capability (35500000 = NAVAL.SYSTEMS)
    db_cursor.execute(
        "insert into programmes (name, country, stage, source_id, naics_code, external_ref, organization_id) "
        "values (%s,%s,%s,%s,%s,%s,%s) returning id",
        ("Test Fixture: Warship Hull Maintenance", "DEU", "rfp_issued", source_id,
         "35500000", f"partner-own-{uuid.uuid4().hex}", buyer_id),
    )
    own_programme_id = db_cursor.fetchone()["id"]

    # Programme B — SAME buyer, DIFFERENT capability, won by a company
    db_cursor.execute(
        "insert into programmes (name, country, stage, source_id, naics_code, external_ref, organization_id) "
        "values (%s,%s,%s,%s,%s,%s,%s) returning id",
        ("Test Fixture: Land Vehicle Supply", "DEU", "contract_awarded", source_id,
         partner_cpv, f"partner-other-{uuid.uuid4().hex}", buyer_id),
    )
    partner_programme_id = db_cursor.fetchone()["id"]

    partner_name = f"Test Partner Vehicles {uuid.uuid4().hex[:8]}"
    db_cursor.execute(
        "insert into organizations (name, org_type, country, classification) "
        "values (%s, 'oem', 'DEU', 'public') returning id",
        (partner_name,),
    )
    partner_org_id = db_cursor.fetchone()["id"]
    db_cursor.execute(
        "insert into contract_awards (programme_id, winner_organization_id, source_id) values (%s,%s,%s)",
        (partner_programme_id, partner_org_id, source_id),
    )

    # Give the tenant a product with the confirmed capability, and an
    # opportunity against programme A so that buyer becomes a target.
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Hull Coating System", "description": "Naval hull maintenance", "trl": 7},
    ).json()["id"]
    db_cursor.execute(
        "insert into product_capabilities (product_id, capability_id, classified_by) values (%s,%s,'analyst')",
        (product_id, capability_id),
    )
    client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    return partner_name, buyer_name


def test_complementary_winner_appears_as_partner(client, auth_headers, db_cursor):
    # 35400000 = LAND.SYSTEMS, different from the tenant's NAVAL.SYSTEMS
    partner_name, buyer_name = _seed_shared_buyer_scenario(
        client, auth_headers, db_cursor, "35400000", "NAVAL.SYSTEMS"
    )
    body = client.get("/intelligence/partners", headers=auth_headers).json()
    assert body["has_confirmed_capabilities"] is True
    assert body["has_target_buyers"] is True

    match = next((p for p in body["partners"] if p["organization_name"] == partner_name), None)
    assert match is not None, "complementary winner from a shared buyer should be a partner"
    assert match["shared_buyer_count"] >= 1
    assert buyer_name in match["shared_buyers"]
    assert any(c["capability_code"] == "LAND.SYSTEMS" for c in match["complementary_capabilities"])


def test_same_capability_winner_is_excluded_as_competitor_not_partner(client, auth_headers, db_cursor):
    # 35500000 = NAVAL.SYSTEMS — the SAME capability the tenant holds,
    # so this winner is a competitor and must NOT appear as a partner.
    partner_name, _ = _seed_shared_buyer_scenario(
        client, auth_headers, db_cursor, "35500000", "NAVAL.SYSTEMS"
    )
    body = client.get("/intelligence/partners", headers=auth_headers).json()
    names = [p["organization_name"] for p in body["partners"]]
    assert partner_name not in names
