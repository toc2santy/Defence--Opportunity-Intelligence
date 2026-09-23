"""
Integration tests for the two Report-Intel-review follow-ups added
together: `products.certifications` (recorded, never scored) and the
set-aside/owner-awareness additions to Next-Best-Action.
"""

import uuid


def test_certifications_round_trip_and_are_not_used_for_classification(client, auth_headers):
    """
    Certifications are for reference/pitching only — a certification
    claim isn't independently verifiable from this platform's own
    data, so it must never influence which capability gets suggested.
    """
    create_resp = client.post(
        "/products", headers=auth_headers,
        json={
            "name": "Cert Test Product",
            "description": "Encrypted communication, long-range, UAV integration",
            "trl": 7,
            "certifications": ["ITAR-registered", "ISO 9001", "AS9100"],
        },
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    listed = client.get("/products", headers=auth_headers).json()
    row = next(p for p in listed if p["id"] == product_id)
    assert row["certifications"] == ["ITAR-registered", "ISO 9001", "AS9100"]

    # Classification is unaffected either way — same candidates with
    # or without certifications on an otherwise-identical description.
    with_certs = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()

    plain_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Plain Product", "description": "Encrypted communication, long-range, UAV integration"},
    ).json()["id"]
    without_certs = client.post(f"/products/{plain_id}/classify", headers=auth_headers).json()

    assert (
        sorted(c["code"] for c in with_certs["candidates"])
        == sorted(c["code"] for c in without_certs["candidates"])
    )


def test_product_without_certifications_lists_an_empty_list_not_null(client, auth_headers):
    product_id = client.post(
        "/products", headers=auth_headers, json={"name": "No Certs", "description": "x"},
    ).json()["id"]
    listed = client.get("/products", headers=auth_headers).json()
    row = next(p for p in listed if p["id"] == product_id)
    assert row["certifications"] == []


def _seed_and_match_with_set_aside(client, auth_headers, db_cursor, naics_code, set_aside_code):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-nba-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref, set_aside_code, set_aside_description)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Secure UAV Data Link Modernisation", "United States", "rfp_issued",
         source_id, naics_code, fixture_ref, set_aside_code, "Total Small Business Set-Aside"),
    )
    programme_id = str(db_cursor.fetchone()["id"])
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Secure UAV Data Link", "description": "Encrypted communication, long-range, UAV integration"},
    ).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    uav = next(c for c in candidates if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav['capability_id']}/confirm", headers=auth_headers)
    matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]
    # Real ingested SAM.gov programmes can share the same NAICS code —
    # pick the match that belongs to THIS fixture programme specifically,
    # not just matches[0].
    fixture_match = next(m for m in matches if m["programme_id"] == programme_id)
    return fixture_match["opportunity_id"]


def test_fresh_match_carries_the_set_aside_note_and_no_owner_note(client, auth_headers, db_cursor):
    """
    A freshly-matched opportunity has no owner by construction, and a
    set-aside published on the tender is real data that was already
    being stored (set_aside_code) but never surfaced in next_action.
    """
    opportunity_id = _seed_and_match_with_set_aside(client, auth_headers, db_cursor, "336411", "SBA")
    body = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={}).json()
    assert "No owner assigned yet" in body["next_action"]
    assert "Set" in body["next_action"] and "Aside" in body["next_action"]


def test_assigning_an_owner_removes_the_no_owner_note(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match_with_set_aside(client, auth_headers, db_cursor, "336411", "SBA")
    me = client.get("/auth/me", headers=auth_headers).json()
    body = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "qualified", "owner_user_id": me["user_id"]},
    ).json()
    assert "No owner assigned" not in body["next_action"]
