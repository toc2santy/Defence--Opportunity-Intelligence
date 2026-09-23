"""
Integration tests for PATCH /opportunities/{id}, GET /opportunities/
{id}/history, and Next-Best-Action auto-population on match. Hits
the live API over HTTP.
"""

import uuid


def _seed_and_match(client, auth_headers, db_cursor, naics_code="336411"):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-engagement-fixture-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Secure UAV Data Link Modernisation", "United States",
         "rfp_issued", source_id, naics_code, fixture_ref),
    )

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Secure UAV Data Link", "description": "Encrypted communication, long-range, UAV integration", "trl": 7},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    uav_candidate = next(c for c in classify_resp.json()["candidates"] if c["code"] == "UAV.INTEGRATION")
    client.post(
        f"/products/{product_id}/capabilities/{uav_candidate['capability_id']}/confirm",
        headers=auth_headers,
    )
    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    matches = match_resp.json()["matches"]
    return matches[0]["opportunity_id"]


def test_update_opportunity_requires_auth(client):
    resp = client.patch("/opportunities/00000000-0000-0000-0000-000000000000", json={"stage": "won"})
    assert resp.status_code in (401, 403)


def test_update_opportunity_404_for_unknown_id(client, auth_headers):
    resp = client.patch(
        "/opportunities/00000000-0000-0000-0000-000000000000",
        headers=auth_headers, json={"stage": "won"},
    )
    assert resp.status_code == 404


def test_new_opportunity_gets_an_auto_populated_next_action(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor)

    # History must be empty — nothing has been PATCHed yet.
    hist_resp = client.get(f"/opportunities/{opportunity_id}/history", headers=auth_headers)
    assert hist_resp.status_code == 200
    assert hist_resp.json()["history"] == []

    # An empty PATCH body changes nothing and simply echoes current
    # state — used here to read next_action without a dedicated GET
    # single-opportunity route.
    read_resp = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={})
    assert read_resp.status_code == 200
    body = read_resp.json()
    assert body["stage"] == "lead"
    assert body["next_action"]  # non-empty — populated at creation, not left null


def test_update_opportunity_rejects_invalid_stage(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334512")
    resp = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "not_a_real_stage"},
    )
    assert resp.status_code == 422


def test_update_opportunity_rejects_invalid_due_date(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334513")
    resp = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"due_date": "not-a-date"},
    )
    assert resp.status_code == 422


def test_stage_change_auto_suggests_next_action(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334514")
    resp = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "won"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage"] == "won"
    assert "onboarding" in body["next_action"].lower()


def test_explicit_next_action_overrides_auto_suggestion(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334515")
    resp = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "qualified", "next_action": "Custom manual action"},
    )
    assert resp.status_code == 200
    assert resp.json()["next_action"] == "Custom manual action"


def test_stage_change_recorded_in_history(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334516")
    client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={"stage": "qualified"})
    client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={"stage": "won"})

    resp = client.get(f"/opportunities/{opportunity_id}/history", headers=auth_headers)
    assert resp.status_code == 200
    history = resp.json()["history"]
    assert len(history) == 2
    assert history[0]["after_state"]["stage"] == "won"
    assert history[1]["after_state"]["stage"] == "qualified"


def test_due_date_and_owner_persist(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334517")
    resp = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"due_date": "2026-12-01"},
    )
    assert resp.status_code == 200
    assert resp.json()["due_date"] == "2026-12-01"


def test_tender_opened_is_recorded_without_advancing_the_stage(client, auth_headers, db_cursor):
    """
    Opening a tender to read it is not a decision to pursue it — the
    event must land in the history timeline while leaving the pipeline
    stage exactly where it was.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334518")

    before = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={}).json()
    resp = client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["recorded"] is True

    after = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={}).json()
    assert after["stage"] == before["stage"], "reading a tender must not advance the pipeline"

    history = client.get(f"/opportunities/{opportunity_id}/history", headers=auth_headers).json()["history"]
    assert any(h["action"] == "opportunity.tender_opened" for h in history)


def test_tender_opened_on_unknown_opportunity_is_404(client, auth_headers):
    resp = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000000/tender-opened", headers=auth_headers
    )
    assert resp.status_code == 404


def test_engagement_summary_counts_real_recorded_events(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334519")

    start = client.get("/engagement/summary", headers=auth_headers).json()
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    after = client.get("/engagement/summary", headers=auth_headers).json()

    assert after["tenders_opened"] == start["tenders_opened"] + 1
    # Counted DISTINCT per opportunity — re-reading the same tender is
    # not a second tender seen.
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    again = client.get("/engagement/summary", headers=auth_headers).json()
    assert again["tenders_opened"] == after["tenders_opened"]


def test_opportunity_detail_carries_the_briefing_fields(client, auth_headers, db_cursor):
    """
    The Tender Briefing is assembled entirely from already-stored
    columns — these are the ones it reads, pinned so they can't be
    dropped from the response without a test failing.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334520")
    body = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={}).json()
    for field in (
        "country", "programme_stage", "classification_code", "organization_name", "source_name",
        "contact_name", "contact_email", "contact_email_secondary", "contact_phone", "contact_address",
    ):
        assert field in body, field


def test_reopening_the_same_tender_counts_but_does_not_log_again(client, auth_headers, db_cursor):
    """
    Going back to a tender you already read is the same event
    happening again, not a new one. The counter must advance every
    time while the history timeline keeps exactly one line for it —
    otherwise repeated visits bury real pipeline activity.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334521")

    first = client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers).json()
    assert first["logged_to_history"] is True
    assert first["tender_opened_count"] == 1

    second = client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers).json()
    third = client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers).json()
    assert second["logged_to_history"] is False
    assert third["tender_opened_count"] == 3

    history = client.get(f"/opportunities/{opportunity_id}/history", headers=auth_headers).json()["history"]
    opens = [h for h in history if h["action"] == "opportunity.tender_opened"]
    assert len(opens) == 1, "a re-opened tender must not add a second history row"


def test_opportunity_detail_reports_how_many_times_it_was_opened(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334522")

    fresh = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={}).json()
    assert fresh["tender_opened_count"] == 0
    assert fresh["tender_last_opened_at"] is None

    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)

    body = client.patch(f"/opportunities/{opportunity_id}", headers=auth_headers, json={}).json()
    assert body["tender_opened_count"] == 2
    assert body["tender_last_opened_at"] is not None


def test_engagement_summary_separates_tenders_seen_from_total_visits(client, auth_headers, db_cursor):
    """
    'Tenders opened' answers how many distinct tenders were read;
    'tender_open_events' is the repeat-visit total the counter exists
    to hold. Both are server-recorded, neither is a browser tally.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334523")

    start = client.get("/engagement/summary", headers=auth_headers).json()
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    after = client.get("/engagement/summary", headers=auth_headers).json()

    assert after["tenders_opened"] == start["tenders_opened"] + 1
    assert after["tender_open_events"] == start["tender_open_events"] + 2


def test_opportunity_list_carries_the_open_counter(client, auth_headers, db_cursor):
    """
    The count has to be on the LIST, not only the detail — otherwise
    the dashboard can say how many tenders were opened but never which
    ones, which is the question a user actually asks.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334524")
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)
    client.post(f"/opportunities/{opportunity_id}/tender-opened", headers=auth_headers)

    listed = client.get("/opportunities", headers=auth_headers).json()
    row = next(o for o in listed if o["id"] == opportunity_id)
    assert row["tender_opened_count"] == 2
    assert row["tender_last_opened_at"] is not None

    untouched = [o for o in listed if o["id"] != opportunity_id]
    assert all(o["tender_opened_count"] == 0 for o in untouched)


def test_opportunity_list_carries_the_programme_stage(client, auth_headers, db_cursor):
    """
    The government's own lifecycle stage for the tender must reach
    the dashboard LIST, not just the detail view — otherwise an
    already-awarded programme can be handed to a customer as a live
    opportunity purely because it scored well. Real case: an
    LVCR-classified product matched an already-awarded programme at a
    high score.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="334525")
    db_cursor.execute(
        "select programme_id from opportunities where id = %s", (opportunity_id,)
    )
    programme_id = db_cursor.fetchone()["programme_id"]
    db_cursor.execute(
        "update programmes set stage = 'contract_awarded' where id = %s", (str(programme_id),)
    )
    db_cursor.connection.commit()

    listed = client.get("/opportunities", headers=auth_headers).json()
    row = next(o for o in listed if o["id"] == opportunity_id)
    assert row["programme_stage"] == "contract_awarded"
