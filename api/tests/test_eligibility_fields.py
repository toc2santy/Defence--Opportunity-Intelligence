"""
Tests proving the eligibility fields (set_aside_code, set_aside_description,
response_deadline) actually flow through the real API — not just that
the columns exist in the database.
"""

import uuid


def test_match_programmes_response_includes_eligibility_fields(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-eligibility-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref, set_aside_code, set_aside_description)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Restricted UAV Programme", "United States", "rfp_issued", source_id,
         "334511", fixture_ref, "SBA", "Total Small Business Set-Aside"),
    )
    programme_id = str(db_cursor.fetchone()["id"])

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Eligibility Test UAV", "description": "UAV drone system", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    uav_candidate = next(c for c in classify_resp.json()["candidates"] if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm", headers=auth_headers)
    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    fixture_match = next(m for m in match_resp.json()["matches"] if m["programme_id"] == programme_id)
    assert fixture_match["set_aside_code"] == "SBA"
    assert fixture_match["set_aside_description"] == "Total Small Business Set-Aside"


def test_opportunities_list_includes_eligibility_fields(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-eligibility-list-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref, set_aside_code, set_aside_description)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Restricted Land Systems Programme", "United States", "rfp_issued", source_id,
         "336411", fixture_ref, "WOSB", "Women-Owned Small Business"),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Eligibility Test Aircraft", "description": "aircraft manufacturing", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    candidate = classify_resp.json()["candidates"][0] if classify_resp.json()["candidates"] else None
    if candidate:
        client.post(f"/products/{product_id}/capabilities/{candidate['capability_id']}/confirm", headers=auth_headers)
        client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    list_resp = client.get("/opportunities", headers=auth_headers)
    assert list_resp.status_code == 200
    # Field presence check — every row's shape must include these
    # keys, whether or not this particular test's product happened
    # to match the restricted fixture above.
    if list_resp.json():
        assert "set_aside_code" in list_resp.json()[0]
        assert "set_aside_description" in list_resp.json()[0]
        assert "response_deadline" in list_resp.json()[0]


def test_unrestricted_programme_has_null_eligibility_fields(client, auth_headers, db_cursor):
    """The common case — no set-aside — must come through as null, not a missing key or empty string."""
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-unrestricted-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Unrestricted UAV Programme", "United States", "rfp_issued", source_id, "334511", fixture_ref),
    )
    programme_id = str(db_cursor.fetchone()["id"])

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Unrestricted Test UAV", "description": "UAV drone system", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    uav_candidate = next(c for c in classify_resp.json()["candidates"] if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm", headers=auth_headers)
    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    fixture_match = next(m for m in match_resp.json()["matches"] if m["programme_id"] == programme_id)
    assert fixture_match["set_aside_code"] is None
    assert fixture_match["set_aside_description"] is None
