"""
Integration tests for GET /intelligence/programmes/browse — the
shared drill-down endpoint every intelligence module's aggregate
numbers (Market Intelligence's "US 403 tenders", Procurement
Intelligence's per-stage/per-source counts, Competitor/Partner
Intelligence's award counts) now link to, added after a user asked
directly to see the real tenders behind a number instead of trusting
it blind.
"""


def test_browse_requires_auth(client):
    resp = client.get("/intelligence/programmes/browse")
    assert resp.status_code in (401, 403)


def test_browse_no_filter_returns_shape(client, auth_headers):
    resp = client.get("/intelligence/programmes/browse?limit=5", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total", "returned", "programmes"):
        assert key in body
    assert body["returned"] <= 5
    assert body["total"] >= body["returned"]
    if body["programmes"]:
        row = body["programmes"][0]
        for key in ("id", "name", "country", "stage", "source_name"):
            assert key in row
        # The hard scope rule from Tender Briefing (contact_* is only
        # ever read via the ONE programme it belongs to, never listed
        # or aggregated) must hold here too — a browse/list view is
        # exactly the aggregation that rule exists to prevent.
        for forbidden in ("contact_name", "contact_email", "contact_phone", "contact_address"):
            assert forbidden not in row


def test_browse_country_filter_matches_only_that_country(client, auth_headers):
    resp = client.get("/intelligence/programmes/browse?country=United%20States&limit=10", headers=auth_headers)
    body = resp.json()
    for row in body["programmes"]:
        assert row["country"] == "United States"


def test_browse_stage_filter_matches_only_that_stage(client, auth_headers):
    resp = client.get("/intelligence/programmes/browse?stage=contract_awarded&limit=10", headers=auth_headers)
    body = resp.json()
    for row in body["programmes"]:
        assert row["stage"] == "contract_awarded"


def test_browse_limit_is_capped_at_500(client, auth_headers):
    resp = client.get("/intelligence/programmes/browse?limit=99999", headers=auth_headers)
    body = resp.json()
    assert body["returned"] <= 500


def test_browse_excludes_test_fixture_rows(client, auth_headers):
    resp = client.get("/intelligence/programmes/browse?limit=500", headers=auth_headers)
    body = resp.json()
    names = [row["name"] for row in body["programmes"]]
    assert not any(n.startswith("Test Fixture:") for n in names)
