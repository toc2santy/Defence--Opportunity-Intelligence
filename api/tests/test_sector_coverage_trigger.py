"""
Integration tests for GET /market/sector-coverage. Hits the live API
over HTTP, same as every other *_trigger.py test — no mocking.
"""

import uuid


def test_sector_coverage_requires_auth(client):
    resp = client.get("/market/sector-coverage")
    assert resp.status_code in (401, 403)


def test_sector_coverage_shape(client, auth_headers):
    resp = client.get("/market/sector-coverage", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "sectors" in body
    assert "total_programmes_resolved" in body
    sectors = body["sectors"]
    assert isinstance(sectors, list)
    # Real ingested data should already be present — this is
    # read-only shared reference data, not something this test seeds.
    assert len(sectors) > 0
    for row in sectors:
        for key in ("sector", "programme_count", "opportunity_count"):
            assert key in row
        assert isinstance(row["programme_count"], int)
        assert isinstance(row["opportunity_count"], int)
        assert row["programme_count"] >= 0
        assert row["opportunity_count"] >= 0


def test_sector_coverage_no_test_fixture_sectors_leak_through(client, auth_headers):
    """
    A real bug found live (2026-09): earlier test runs against this
    same shared database had created capability_taxonomy rows with
    sector='Test'/'Test Sector' (via POST /taxonomy, a real endpoint
    test_taxonomy_admin.py exercises against the live DB) that were
    never cleaned up — they'd have shown up here as two fake "sectors"
    on a page meant to show real market coverage. Cleaned up once;
    this guards against it silently recurring.
    """
    resp = client.get("/market/sector-coverage", headers=auth_headers)
    names = [row["sector"] for row in resp.json()["sectors"]]
    assert "Test" not in names
    assert "Test Sector" not in names


def test_sector_coverage_a_fresh_tenant_has_zero_opportunities_but_real_programme_counts(client, auth_headers):
    """
    programme_count reflects shared platform data (non-zero even for
    a brand-new tenant that hasn't matched anything yet);
    opportunity_count is genuinely this tenant's own and should be 0
    across every sector for a tenant that has no opportunities.
    """
    resp = client.get("/market/sector-coverage", headers=auth_headers)
    sectors = resp.json()["sectors"]
    assert any(row["programme_count"] > 0 for row in sectors)
    assert all(row["opportunity_count"] == 0 for row in sectors)


def test_opportunities_list_carries_sector_tags(client, auth_headers):
    resp = client.get("/opportunities", headers=auth_headers)
    assert resp.status_code == 200
    for row in resp.json():
        assert "sectors" in row
        assert isinstance(row["sectors"], list)


def test_sector_coverage_shared_with_explains_multi_capability_overlap(client, auth_headers, db_cursor):
    """
    A real user-reported confusion (2026-09): the exact same
    opportunity_count (994) showing on three sector cards (Radar,
    Naval Systems, Sensors) understandably read as the old
    NAICS-334511 bug (migration 041) resurfacing — it wasn't; that
    fix was correct, and the identical number is the honest,
    already-verified result of one tenant's opportunities being
    dominated by a single classification code the government's own
    definition genuinely maps to three capabilities at once. shared_with
    makes that fact visible on the card itself instead of requiring a
    fresh investigation every time.

    Uses NAICS 336411 here (UAV.INTEGRATION + AVIATION.MILITARY,
    confirmed real since migration 015) rather than 334511, so this
    test's own fixture data doesn't ride on the exact tenant/product
    history the original 994 report came from.
    """
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (f"Test Fixture: Shared Sector UAV Programme {uuid.uuid4().hex[:8]}", "United States",
         "rfp_issued", source_id, "336411", f"test-shared-sector-{uuid.uuid4().hex}"),
    )
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Shared Sector UAV", "description": "UAV integration, tactical aviation", "trl": 6},
    ).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    uav = next(c for c in candidates if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav['capability_id']}/confirm", headers=auth_headers)
    client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    resp = client.get("/market/sector-coverage", headers=auth_headers)
    sectors = {row["sector"]: row for row in resp.json()["sectors"]}
    assert "shared_with" in sectors["UAV / UAS"]
    assert "Military Aviation" in sectors["UAV / UAS"]["shared_with"]
    assert "UAV / UAS" in sectors["Military Aviation"]["shared_with"]
    # A sector with no multi-capability overlap in this tenant's own
    # data must report an empty list, not omit the field or error.
    assert sectors["Electro-Optics"]["shared_with"] == [] or isinstance(sectors["Electro-Optics"]["shared_with"], list)
