"""
Tests for the generalized ingestion source registry
(app.ingestion_common) — verified through the real running API,
consistent with how the scheduler itself is already tested
(test_scheduled_ingestion_and_notifications.py). ingestion_common.py
needs SQLAlchemy to import, which isn't installed in the host
.venv (only pytest/httpx/psycopg2-binary are, by design — the
container has the real dependency set), so this is proven live
rather than as a host-side unit test.
"""


def test_sources_status_lists_registered_sources(client, auth_headers):
    resp = client.get("/ingestion/sources/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "sources" in body
    assert len(body["sources"]) >= 3  # sam_gov + uk_find_a_tender + ted_eu

    sam_gov_entry = next((s for s in body["sources"] if s["code"] == "sam_gov"), None)
    assert sam_gov_entry is not None, "sam_gov should be registered — this is the source being refactored, not removed"
    assert sam_gov_entry["display_name"] == "SAM.gov Contract Opportunities API"
    assert sam_gov_entry["scheduled"] is True  # confirms the startup registration loop actually ran
    assert "api_key_configured" in sam_gov_entry
    assert isinstance(sam_gov_entry["api_key_configured"], bool)

    uk_entry = next((s for s in body["sources"] if s["code"] == "uk_find_a_tender"), None)
    assert uk_entry is not None, (
        "uk_find_a_tender should be registered — this is the real proof the generalized "
        "registry works for a second, genuinely different source"
    )
    assert uk_entry["display_name"] == "UK Find a Tender Service"
    assert uk_entry["scheduled"] is True
    # no API key needed for this source — api_key_configured should
    # report True unconditionally (see the api_key_env_var=None case
    # in get_ingestion_sources_status), not depend on any env var
    assert uk_entry["api_key_configured"] is True


def test_sources_status_requires_authentication(client):
    resp = client.get("/ingestion/sources/status")
    assert resp.status_code in (401, 403)


def test_old_scheduler_status_endpoint_still_works_unchanged(client, auth_headers):
    """
    Regression check — the refactor to a registry-based scheduler
    must not have broken the original, still-in-use
    /ingestion/scheduler-status endpoint or its response shape.
    """
    resp = client.get("/ingestion/scheduler-status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["scheduled"] is True
    assert body["next_run_time"] is not None
    assert "rotation" in body
    assert "current_rotation_index" in body["rotation"]
    assert "naics_codes_next_run" in body["rotation"]
