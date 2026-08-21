"""
Phase 3 integration tests. These seed a CONTROLLED programme record
directly in the database (via db_cursor) rather than relying on
whatever real SAM.gov data happens to already be in your database
from earlier live ingestion — that keeps these tests deterministic
and independent of external state, while still exercising the real
API end to end for everything else.
"""

import uuid


def test_confirmed_capability_matches_naics_and_keyword_programme(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]

    fixture_ref = f"test-fixture-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Secure UAV Data Link Modernisation", "United States",
         "rfp_issued", source_id, "334511", fixture_ref),
    )
    programme_id = str(db_cursor.fetchone()["id"])

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Secure UAV Data Link", "description": "Encrypted communication, long-range, UAV integration", "trl": 7},
    )
    product_id = create_resp.json()["id"]

    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    candidates = classify_resp.json()["candidates"]
    uav_candidate = next((c for c in candidates if c["code"] == "UAV.INTEGRATION"), None)
    assert uav_candidate is not None, "expected UAV.INTEGRATION among the classification candidates"

    confirm_resp = client.post(
        f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm",
        headers=auth_headers,
    )
    assert confirm_resp.status_code == 200

    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    assert match_resp.status_code == 200
    matches = match_resp.json()["matches"]

    fixture_match = next((m for m in matches if m["programme_id"] == programme_id), None)
    assert fixture_match is not None, "expected the seeded fixture programme to appear in matches"
    assert fixture_match["naics_match"] is True
    assert "uav" in fixture_match["matched_keywords"]
    # naics bonus (3) + at least the "uav" keyword weight (3) = 6+
    assert fixture_match["total_score"] >= 6
    assert fixture_match["confidence"] == "high"

    # and the real opportunities table should now have a row for it
    opp_resp = client.get("/opportunities", headers=auth_headers)
    assert opp_resp.status_code == 200
    opps = opp_resp.json()
    assert any(o["programme_name"] == "Test Fixture: Secure UAV Data Link Modernisation" for o in opps)


def test_unconfirmed_capability_produces_no_matches(client, auth_headers):
    # classify but deliberately never confirm — this is the
    # human-in-the-loop gate: an ai_suggested capability alone
    # should never generate a customer-visible opportunity.
    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Tactical Radar System", "description": "Doppler radar for detection", "trl": 8},
    )
    product_id = create_resp.json()["id"]
    client.post(f"/products/{product_id}/classify", headers=auth_headers)

    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    assert match_resp.status_code == 200
    assert match_resp.json()["matches"] == []


def test_rerunning_match_updates_the_same_opportunity_not_duplicates(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Repeat Match UAV Programme", "United States", "rfp_issued", source_id, "334511", fixture_ref),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Drone Platform", "description": "UAV drone system", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    candidates = classify_resp.json()["candidates"]
    uav_candidate = next(c for c in candidates if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm", headers=auth_headers)

    resp1 = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    resp2 = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    ids1 = {m["opportunity_id"] for m in resp1.json()["matches"]}
    ids2 = {m["opportunity_id"] for m in resp2.json()["matches"]}
    assert ids1 == ids2, "re-running the match should update existing opportunity rows, not create new ones"
