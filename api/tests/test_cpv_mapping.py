"""
Proves the real point of the CPV mapping migration: a product
classified with a capability that has a CPV mapping now genuinely
matches a UK-sourced (CPV-coded) programme, not just a NAICS-coded
one. Uses a controlled fixture programme with a real verified CPV
code (35410000, armoured military vehicles), same deterministic-
fixture pattern as the existing Phase 3 tests — not dependent on
whatever's actually in the live-ingested UK data at test time.
"""

import uuid


def test_cpv_mapped_capability_matches_uk_sourced_programme(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'UK Find a Tender Service'")
    source_row = db_cursor.fetchone()
    assert source_row is not None, (
        "UK Find a Tender source must be seeded — run db/migrations/009_uk_find_a_tender.sql first"
    )
    source_id = source_row["id"]

    fixture_ref = f"test-fixture-uk-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Armoured Vehicle Upgrade Programme", "United Kingdom",
         "rfp_issued", source_id, "35410000", fixture_ref),  # real verified CPV code, armoured military vehicles
    )
    programme_id = str(db_cursor.fetchone()["id"])

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Armoured Vehicle Platform", "description": "Land systems armoured vehicle upgrade kit", "trl": 7},
    )
    product_id = create_resp.json()["id"]

    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    land_candidate = next(
        (c for c in classify_resp.json()["candidates"] if c["code"] == "LAND.SYSTEMS"), None
    )
    assert land_candidate is not None, "expected LAND.SYSTEMS among classification candidates"

    confirm_resp = client.post(
        f"/products/{product_id}/capabilities/{land_candidate['capability_id']}/confirm",
        headers=auth_headers,
    )
    assert confirm_resp.status_code == 200

    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    assert match_resp.status_code == 200
    matches = match_resp.json()["matches"]

    fixture_match = next((m for m in matches if m["programme_id"] == programme_id), None)
    assert fixture_match is not None, (
        "expected the UK CPV-coded fixture programme to appear in matches — "
        "this is the actual proof CPV mapping connects UK data to Phase 3 matching"
    )
    assert fixture_match["naics_match"] is True  # field name kept for backward compat — see programme_matching.py
    assert fixture_match["matched_classification_code"] == "35410000"


def test_uav_capability_has_no_cpv_mapping_yet_honest_gap(client, auth_headers, db_cursor):
    """
    UAV.INTEGRATION is this whole project's flagship example — and
    it's deliberately NOT mapped to any CPV code (no verified,
    defensible code was found). This test exists specifically to
    catch the day someone adds a real UAV CPV mapping without
    updating this test — at which point it should be updated to
    reflect the new reality, not silently left describing a gap
    that no longer exists.
    """
    db_cursor.execute(
        """
        select count(*) as cnt from taxonomy_cpv_mapping tcm
        join capability_taxonomy ct on ct.id = tcm.capability_id
        where ct.code = 'UAV.INTEGRATION'
        """
    )
    row = db_cursor.fetchone()
    assert row["cnt"] == 0, (
        "UAV.INTEGRATION now has a CPV mapping — if this is intentional, update this test "
        "(it existed to document a real, stated gap, not to prevent fixing it)"
    )
