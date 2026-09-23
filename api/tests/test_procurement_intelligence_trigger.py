"""
Integration tests for GET /intelligence/procurement. Hits the live
API over HTTP, same as every other *_trigger.py test.
"""

# Mirrors app.procurement_intelligence.PROGRAMME_STAGES — not
# imported directly, since trigger tests exercise the live API over
# HTTP only and don't import app modules (see conftest.py).
PROGRAMME_STAGES = [
    "early_concept", "requirement_defined", "rfi_issued",
    "rfp_issued", "contract_awarded", "in_service",
]


def test_procurement_requires_auth(client):
    resp = client.get("/intelligence/procurement")
    assert resp.status_code in (401, 403)


def test_procurement_funnel_shape(client, auth_headers):
    resp = client.get("/intelligence/procurement", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_programmes", "funnel", "by_source", "top_countries"):
        assert key in body
    assert [f["stage"] for f in body["funnel"]] == list(PROGRAMME_STAGES)


def test_procurement_funnel_counts_sum_to_total(client, auth_headers):
    body = client.get("/intelligence/procurement", headers=auth_headers).json()
    assert sum(f["count"] for f in body["funnel"]) == body["total_programmes"]


def test_country_count_is_the_true_total_not_the_capped_list(client, auth_headers):
    """
    top_countries is capped at 15 for display; country_count is the
    real distinct total. Reading the former's length as the country
    count silently understates coverage — a mistake made once while
    wiring the Presentation page KPI, pinned here so it stays fixed.
    """
    body = client.get("/intelligence/procurement", headers=auth_headers).json()
    assert "country_count" in body
    assert body["country_count"] >= len(body["top_countries"])
    assert len(body["top_countries"]) <= 15


def test_procurement_by_source_funnels_use_same_stage_order(client, auth_headers):
    body = client.get("/intelligence/procurement", headers=auth_headers).json()
    for source in body["by_source"]:
        assert [f["stage"] for f in source["funnel"]] == list(PROGRAMME_STAGES)


def test_procurement_total_matches_public_platform_stats(client, auth_headers):
    """
    A real bug, found live (2026-09) by a user noticing Home and
    Presentation showed different "Live Tenders Tracked" numbers:
    this route used to count every row in `programmes`, including
    ~658 "Test Fixture: ..." rows the test suite itself seeds into
    the same shared table, while /public/platform-stats already
    excluded them via not_a_test_fixture(). Both numbers are read
    live as real, customer-facing claims (Presentation explicitly
    says so in its own UI copy) — they must always agree.
    """
    procurement_total = client.get("/intelligence/procurement", headers=auth_headers).json()["total_programmes"]
    public_total = client.get("/public/platform-stats").json()["programme_count"]
    assert procurement_total == public_total
