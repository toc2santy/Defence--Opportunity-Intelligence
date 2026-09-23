"""
Integration tests for GET /products/{id}/intel-report — Report Intel.

Hits the live API over HTTP. The assertions deliberately pin the two
properties that make this report trustworthy rather than decorative:
every engine block is present whether or not it found anything, and
an engine that found nothing carries a stated reason instead of
silently rendering as an empty card.
"""

import uuid

CANONICAL_ENGINES = {
    "01": "Capability Intelligence",
    "02": "Market Intelligence",
    "03": "Programme Intelligence",
    "04": "Customer Intelligence",
    "05": "OEM & Partner Matching",
    "06": "Competitor Intelligence",
    "07": "Opportunity Intelligence",
    "08": "Procurement Intelligence",
    "09": "Engagement Intelligence",
    "10": "Next-Best-Action",
}


def _seed_matched_product(client, auth_headers, db_cursor, naics_code, stage="rfp_issued", response_deadline=None, name=None):
    # A unique name per call, unless the caller wants the shared
    # generic one — this same shared DB accumulates one row per past
    # test run with no cleanup (see this project's own established
    # pattern), so a test that needs to find exactly ITS OWN row
    # (not just any row with the shared generic label) must give it a
    # name nothing else could share.
    programme_name = name or "Test Fixture: Secure UAV Data Link Modernisation"
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref, response_deadline)
        values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (programme_name, "United States",
         stage, source_id, naics_code, f"test-report-{uuid.uuid4().hex}", response_deadline),
    )
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Secure UAV Data Link",
              "description": "Encrypted communication, long-range, UAV integration", "trl": 7},
    ).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    uav = next(c for c in candidates if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav['capability_id']}/confirm", headers=auth_headers)
    matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]
    return product_id, matches


def test_intel_report_requires_auth(client):
    resp = client.get("/products/00000000-0000-0000-0000-000000000000/intel-report")
    assert resp.status_code in (401, 403)


def test_intel_report_404_for_unknown_product(client, auth_headers):
    resp = client.get(
        "/products/00000000-0000-0000-0000-000000000000/intel-report", headers=auth_headers
    )
    assert resp.status_code == 404


def test_report_numbering_matches_the_customer_facing_engine_list(client, auth_headers, db_cursor):
    """
    The Engines tab numbers these 01-10. A report that renumbered them
    would hand the customer two contradictory schemes for the same ten
    engines.
    """
    product_id, _ = _seed_matched_product(client, auth_headers, db_cursor, "336411")
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    assert {e["engine"]: e["title"] for e in report["engines"]} == CANONICAL_ENGINES
    assert [e["engine"] for e in report["engines"]] == sorted(CANONICAL_ENGINES)


def test_every_engine_block_is_present_even_when_it_found_nothing(client, auth_headers, db_cursor):
    product_id, _ = _seed_matched_product(client, auth_headers, db_cursor, "334512")
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    assert report["engines_total"] == 10
    assert len(report["engines"]) == 10
    for e in report["engines"]:
        # An engine that produced nothing must say why — an empty card
        # with no reason reads as a broken engine.
        assert e["ran"] or e["note"], f"engine {e['engine']} is silent about why it has nothing"
        assert e["what_it_does"]


def test_report_reflects_the_real_cycle_state(client, auth_headers, db_cursor):
    product_id, matches = _seed_matched_product(client, auth_headers, db_cursor, "334513")

    before = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()
    assert before["cycle"]["classified"] is True
    assert before["cycle"]["confirmed"] is True
    assert before["cycle"]["matched"] is True
    # Not briefed yet — the last step of the cycle has genuinely not happened.
    assert before["cycle"]["briefed"] is False
    assert before["cycle"]["tenders_opened"] == 0

    client.post(f"/opportunities/{matches[0]['opportunity_id']}/tender-opened", headers=auth_headers)
    after = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()
    assert after["cycle"]["briefed"] is True
    assert after["cycle"]["tenders_opened"] == 1

    engagement = next(e for e in after["engines"] if e["engine"] == "09")
    assert engagement["ran"] is True


def test_capability_engine_reports_confirmed_versus_suggested(client, auth_headers, db_cursor):
    product_id, _ = _seed_matched_product(client, auth_headers, db_cursor, "334514")
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    capability = next(e for e in report["engines"] if e["engine"] == "01")
    assert capability["ran"] is True
    confirmed = [r for r in capability["rows"] if r["confirmed"]]
    suggested = [r for r in capability["rows"] if not r["confirmed"]]
    assert len(confirmed) == 1, "exactly one capability was confirmed in this fixture"
    # The human-in-the-loop gate must be visible in the report, not
    # flattened into a single 'capabilities' count.
    assert all("Software suggested" in r["status"] for r in suggested)


def test_report_for_an_unstarted_product_says_so_rather_than_failing(client, auth_headers):
    """
    A product with no classification is a legitimate state, not an
    error — the report must render and name the missing step.
    """
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Untouched Product", "description": "nothing done to this yet", "trl": 4},
    ).json()["id"]

    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()
    assert report["cycle"] == {
        "classified": False, "confirmed": False, "matched": False, "briefed": False,
        "capabilities_confirmed": 0, "opportunities": 0, "tenders_opened": 0,
    }
    assert report["engines_ran"] == 0
    capability = next(e for e in report["engines"] if e["engine"] == "01")
    assert "has not been classified" in capability["note"]


def test_every_real_capability_can_reach_award_data(db_cursor):
    """
    Award winners come only from TED and UK Find a Tender, and both
    publish CPV. A capability with no CPV mapping therefore cannot
    produce a competitor or partner result at all — which the v1
    22-row mapping left true for UAV, secure comms, sonar, C4ISR,
    aerospace, space and autonomy. This pins the gap closed.
    """
    db_cursor.execute("""
        select ct.code
        from capability_taxonomy ct
        where not exists (
            select 1 from taxonomy_cpv_mapping m where m.capability_id = ct.id
        )
    """)
    unmapped = [r["code"] for r in db_cursor.fetchall() if not r["code"].startswith("TEST")]
    assert unmapped == [], f"capabilities with no CPV mapping cannot answer Competitor/Partner: {unmapped}"


def test_competitor_rows_carry_the_evidence_behind_the_count(client, auth_headers, db_cursor):
    """
    A company name with an award count and nothing else is an
    unexplained number. Each row must name which of YOUR capability
    areas the award fell in, and the real notices behind it.
    """
    product_id, _ = _seed_matched_product(client, auth_headers, db_cursor, "334515")
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    competitor = next(e for e in report["engines"] if e["engine"] == "06")
    assert competitor["ran"] is True, "UAV.INTEGRATION now has CPV mappings, so this must resolve"
    for row in competitor["rows"]:
        assert row["detail"], "row must name the shared capability area"
        assert row["examples"], "row must carry the real notices behind the count"


def test_competitor_detail_names_only_shared_capability_areas(client, auth_headers, db_cursor):
    product_id, _ = _seed_matched_product(client, auth_headers, db_cursor, "334516")
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    confirmed = {
        r["label"] for r in next(e for e in report["engines"] if e["engine"] == "01")["rows"]
        if r["confirmed"]
    }
    competitor = next(e for e in report["engines"] if e["engine"] == "06")
    for row in competitor["rows"]:
        named = set(row["detail"].split(" · "))
        # Naming a competitor's unrelated capability areas would
        # overstate the overlap the row claims.
        assert named <= confirmed, f"{named - confirmed} is not a capability this product holds"


def test_blocks_report_the_true_total_not_just_what_was_sent(client, auth_headers, db_cursor):
    """
    A headline reading "162 buying organisations" above six visible
    rows is only honest if the total and the sent rows are both
    stated — the UI uses these to offer "see all".
    """
    product_id, _ = _seed_matched_product(client, auth_headers, db_cursor, "334517")
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    for e in report["engines"]:
        assert e["total_rows"] >= len(e["rows"])
        assert e["preview"] >= 1


def test_scoring_engine_flags_an_already_awarded_tender_as_historical(client, auth_headers, db_cursor):
    """
    A real user-reported confusion (2026-09): a tender scored high
    here (a genuinely good capability/keyword match) but did not
    appear in the Opportunity Dashboard's Active tab, because the
    government's own stage already shows it awarded — the dashboard
    correctly moves it to Historical, but the report itself said
    nothing, so the score alone looked like "go bid on this".
    historical_reason must mirror the dashboard's own
    isClosedTender/programmeStatusRemark logic: a 'contract_awarded'
    stage is always historical, regardless of score or confidence.
    """
    unique_name = f"Test Fixture: Awarded UAV Programme {uuid.uuid4().hex[:8]}"
    product_id, _ = _seed_matched_product(
        client, auth_headers, db_cursor, "336411", stage="contract_awarded", name=unique_name,
    )
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    scoring = next(e for e in report["engines"] if e["engine"] == "07")
    row = next(r for r in scoring["rows"] if r["label"] == unique_name)
    assert row["historical_reason"] == "Already awarded — not open for bidding"


def test_scoring_engine_flags_a_past_deadline_as_historical(client, auth_headers, db_cursor):
    unique_name = f"Test Fixture: Expired UAV Programme {uuid.uuid4().hex[:8]}"
    product_id, _ = _seed_matched_product(
        client, auth_headers, db_cursor, "336411", stage="rfp_issued",
        response_deadline="2020-01-01T00:00:00-05:00", name=unique_name,
    )
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    scoring = next(e for e in report["engines"] if e["engine"] == "07")
    row = next(r for r in scoring["rows"] if r["label"] == unique_name)
    assert row["historical_reason"] == "Response deadline has passed"


def test_scoring_engine_does_not_flag_a_live_open_tender(client, auth_headers, db_cursor):
    unique_name = f"Test Fixture: Live UAV Programme {uuid.uuid4().hex[:8]}"
    product_id, _ = _seed_matched_product(
        client, auth_headers, db_cursor, "336411", stage="rfp_issued",
        response_deadline="2099-01-01T00:00:00-05:00", name=unique_name,
    )
    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()

    scoring = next(e for e in report["engines"] if e["engine"] == "07")
    row = next(r for r in scoring["rows"] if r["label"] == unique_name)
    assert row["historical_reason"] is None


def test_nba_engine_excludes_already_closed_tenders(client, auth_headers, db_cursor):
    """
    A real user request (2026-09): a "next action" for an
    already-awarded or deadline-passed tender is not useful data —
    unlike engine 07's score (a fact worth keeping visible with a
    Historical badge), a suggested ACTION on something no longer open
    is not a real suggestion. Next-Best-Action must filter these out
    rather than badge them.
    """
    live_name = f"Test Fixture: Live NBA UAV Programme {uuid.uuid4().hex[:8]}"
    awarded_name = f"Test Fixture: Awarded NBA UAV Programme {uuid.uuid4().hex[:8]}"

    # Two separate products so each seeds its own, independently
    # matched programme — keeps this test from depending on how many
    # OTHER historical fixtures already exist in this shared dev DB.
    live_product_id, live_matches = _seed_matched_product(
        client, auth_headers, db_cursor, "336411", stage="rfp_issued",
        response_deadline="2099-01-01T00:00:00-05:00", name=live_name,
    )
    awarded_product_id, awarded_matches = _seed_matched_product(
        client, auth_headers, db_cursor, "336411", stage="contract_awarded", name=awarded_name,
    )
    # next_action is only populated by a real stage transition (see
    # app/next_best_action.py) — a PATCH with an explicit stage moves
    # both opportunities forward and computes a fresh suggestion.
    client.patch(
        f"/opportunities/{live_matches[0]['opportunity_id']}", headers=auth_headers,
        json={"stage": "qualified"},
    )
    client.patch(
        f"/opportunities/{awarded_matches[0]['opportunity_id']}", headers=auth_headers,
        json={"stage": "qualified"},
    )

    live_report = client.get(f"/products/{live_product_id}/intel-report", headers=auth_headers).json()
    awarded_report = client.get(f"/products/{awarded_product_id}/intel-report", headers=auth_headers).json()

    live_nba = next(e for e in live_report["engines"] if e["engine"] == "10")
    awarded_nba = next(e for e in awarded_report["engines"] if e["engine"] == "10")

    assert any(r["label"] == live_name for r in live_nba["rows"]), "a live tender's next action must still appear"
    # This product's match-programmes call also picks up every OTHER
    # 336411-coded fixture this shared dev DB has accumulated across
    # past test runs (see this file's own established pattern), so
    # the block can still legitimately be "ran": True from those —
    # the one thing this test actually pins is that THIS SPECIFIC
    # awarded tender's own next action does not appear among them.
    assert not any(r["label"] == awarded_name for r in awarded_nba["rows"]), "an awarded tender's next action must be excluded, not shown"


def test_nba_coverage_summary_reflects_a_real_gap(client, auth_headers, db_cursor):
    """
    A real user request (2026-09), following the 30,926-opportunity
    missing-next_action backfill this same investigation found: the
    report must surface a live opportunity with no next_action as a
    visible gap on every render, not require a human to notice by
    hand again.
    """
    name = f"Test Fixture: Coverage Gap UAV Programme {uuid.uuid4().hex[:8]}"
    product_id, matches = _seed_matched_product(
        client, auth_headers, db_cursor, "336411", stage="rfp_issued",
        response_deadline="2099-01-01T00:00:00-05:00", name=name,
    )
    # This product also matches every OTHER accumulated 336411 fixture
    # in this shared dev DB, so matches[0] is not reliably THIS test's
    # own programme — look up its opportunity by the programme's own
    # unique name instead of trusting list order.
    db_cursor.execute(
        """
        select o.id from opportunities o
        join programmes p on p.id = o.programme_id
        where o.product_id = %s and p.name = %s
        """,
        (product_id, name),
    )
    opportunity_id = db_cursor.fetchone()["id"]
    # Simulate the exact real-world gap: a live opportunity whose
    # next_action never got computed (as if created before the
    # feature existed) — directly null it out via the DB, the same
    # state the 30,926 real rows were found in.
    db_cursor.execute("update opportunities set next_action = null where id = %s", (opportunity_id,))

    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()
    coverage = report["nba_coverage"]

    assert coverage["live_missing_next_action"] >= 1
    assert "gap" in coverage["note"]

    nba = next(e for e in report["engines"] if e["engine"] == "10")
    assert not any(r["label"] == name for r in nba["rows"]), "an opportunity with no next_action must not appear in NBA's own rows"
