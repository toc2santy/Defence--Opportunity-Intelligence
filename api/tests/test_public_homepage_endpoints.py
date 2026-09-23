"""
Integration tests for the un-authenticated homepage endpoints:
GET /public/preview-match, /public/platform-stats and
/public/stat-detail. All are reachable with no Authorization header at
all — the point of this suite is to pin exactly that, and to pin what
they must never hand out for free.
"""

import uuid


def test_platform_stats_requires_no_auth(client):
    resp = client.get("/public/platform-stats")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("programme_count", "country_count", "source_count", "buyer_count"):
        assert key in body
        assert isinstance(body[key], int)


def test_preview_match_requires_no_auth(client):
    resp = client.get("/public/preview-match", params={"query": "secure tactical UAV data link"})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("matched_capabilities", "programme_count", "country_count"):
        assert key in body


def test_preview_match_rejects_too_short_query(client):
    resp = client.get("/public/preview-match", params={"query": "ab"})
    assert resp.status_code == 422


def test_preview_match_rejects_too_long_query(client):
    resp = client.get("/public/preview-match", params={"query": "a" * 501})
    assert resp.status_code == 422


def test_preview_match_never_returns_programme_names_or_links(client):
    """
    The whole point of this endpoint is to prove the platform works
    without giving away the real dataset for free — pinned here so a
    future change doesn't accidentally start including programme-
    level detail in the public response.
    """
    resp = client.get("/public/preview-match", params={"query": "secure tactical UAV data link for naval surveillance"})
    body = resp.json()
    assert "programmes" not in body
    assert "matches" not in body
    for cap in body["matched_capabilities"]:
        assert set(cap.keys()) == {"label", "sector"}


def test_stat_detail_requires_no_auth_for_every_metric(client):
    for metric in ("tenders", "countries", "sources", "buyers"):
        resp = client.get("/public/stat-detail", params={"metric": metric})
        assert resp.status_code == 200, metric
        body = resp.json()
        assert body["metric"] == metric
        assert isinstance(body["rows"], list)
        assert body["showing"] == len(body["rows"])


def test_stat_detail_rejects_unknown_metric(client):
    resp = client.get("/public/stat-detail", params={"metric": "everything"})
    assert resp.status_code == 422


def test_stat_detail_never_leaks_apply_links_or_contacts(client):
    """
    These drill-downs exist to prove the homepage numbers are real, not
    to hand the dataset to someone who never signs up — the apply link
    and procurement contact are exactly what an account is for. Pinned
    so a future column addition can't quietly give them away.
    """
    body = client.get("/public/stat-detail", params={"metric": "tenders"}).json()
    for row in body["rows"]:
        for forbidden in ("ui_link", "contact_name", "contact_email", "contact_phone"):
            assert forbidden not in row


def test_stat_detail_excludes_test_fixture_rows(client, db_cursor):
    """
    The test suite seeds fixture programmes into the same shared table
    the homepage counts read from. A prospect clicking "Live Tenders
    Tracked" must never see a row called "Test Fixture: ..." — this
    asserts the public filter holds even with a fixture present.
    """
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        """,
        ("Test Fixture: Should Never Be Public", "United States", "rfp_issued",
         source_id, "336611", f"test-fixture-{uuid.uuid4().hex}"),
    )

    body = client.get("/public/stat-detail", params={"metric": "tenders"}).json()
    assert all("Test Fixture" not in (r["name"] or "") for r in body["rows"])


def test_preview_match_nonsense_query_returns_zero(client):
    resp = client.get("/public/preview-match", params={"query": "asdkjhaskdjhaskjdh"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["programme_count"] == 0
    assert body["matched_capabilities"] == []
