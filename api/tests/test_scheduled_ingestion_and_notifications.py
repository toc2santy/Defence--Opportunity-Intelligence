"""
Tests for the scheduled-ingestion infrastructure and the
"what's new since you last looked" checkpoint mechanism.

The scheduler's actual 24-hour interval can't be tested in a short
pytest run — what CAN be verified is that the job genuinely
registers on API startup (proving the mechanism is wired, not just
present in the code), and that the checkpoint logic behaves
correctly, which is the part that actually matters for the "you
saw this first" claim.
"""

import uuid


def test_scheduler_status_shows_a_registered_job(client, auth_headers):
    resp = client.get("/ingestion/scheduler-status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["scheduled"] is True
    assert body["next_run_time"] is not None, (
        "the scheduled job should have a real next run time — if this is None, "
        "the scheduler didn't actually start on API boot"
    )
    assert body["interval_hours"] > 0


def test_new_opportunities_starts_empty_for_a_fresh_tenant_with_no_data(client, auth_headers):
    resp = client.get("/opportunities/new", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_count"] == 0
    assert body["opportunities"] == []
    assert body["previously_viewed_at"] is None  # never checked before


def test_new_opportunities_shows_matches_then_marks_them_seen(client, auth_headers, db_cursor):
    # seed a controlled programme, same pattern as the Phase 3 tests
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: New Opportunity UAV Programme", "United States", "rfp_issued", source_id, "336411", fixture_ref),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Drone System", "description": "UAV drone platform", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    uav_candidate = next(c for c in classify_resp.json()["candidates"] if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm", headers=auth_headers)
    client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    # first check: should see the new opportunity, and it gets marked seen
    first_check = client.get("/opportunities/new", headers=auth_headers)
    assert first_check.status_code == 200
    assert first_check.json()["new_count"] >= 1

    # second check, immediately after: nothing new, since it was
    # already marked seen by the first call
    second_check = client.get("/opportunities/new", headers=auth_headers)
    assert second_check.status_code == 200
    assert second_check.json()["new_count"] == 0
    assert second_check.json()["previously_viewed_at"] is not None


def test_mark_seen_false_does_not_advance_the_checkpoint(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Peek Without Marking Seen", "United States", "rfp_issued", source_id, "336411", fixture_ref),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Sensor Platform", "description": "UAV sensor system", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    uav_candidate = next(c for c in classify_resp.json()["candidates"] if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm", headers=auth_headers)
    client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)

    # peek without marking seen
    peek = client.get("/opportunities/new?mark_seen=false", headers=auth_headers)
    assert peek.json()["new_count"] >= 1

    # should still show up as new on the next real check, since the
    # peek did not advance the checkpoint
    real_check = client.get("/opportunities/new", headers=auth_headers)
    assert real_check.json()["new_count"] >= 1
