"""
Integration tests for /ingestion/ted-eu/run and registry presence.
"""

import os
import pytest


def test_ted_eu_trigger_requires_admin(client):
    resp = client.post("/ingestion/ted-eu/run", json={})
    assert resp.status_code in (401, 403)


def test_ted_eu_appears_in_sources_status(client, auth_headers):
    resp = client.get("/ingestion/sources/status", headers=auth_headers)
    assert resp.status_code == 200
    sources = resp.json()["sources"]
    ted_entry = next((s for s in sources if s["code"] == "ted_eu"), None)
    assert ted_entry is not None, "ted_eu should be registered in the ingestion source registry"
    assert ted_entry["display_name"] == "EU TED (Tenders Electronic Daily)"
    assert ted_entry["scheduled"] is True
    assert ted_entry["api_key_configured"] is True  # no key needed


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TED_TEST"),
    reason="Set RUN_LIVE_TED_TEST=1 to run this — it hits the real live EU TED API.",
)
def test_ted_eu_trigger_response_shape(client, auth_headers):
    resp = client.post("/ingestion/ted-eu/run", headers=auth_headers, json={"days_back": 90})
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert "records_ingested" in body
    assert "query_used" in body
    assert "date_range" in body
    print(f"\\nTED EU ingestion: {body['records_ingested']} defense-relevant records ingested")
    print(f"Query used: {body['query_used']}")
