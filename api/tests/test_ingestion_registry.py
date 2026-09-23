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


def test_sources_status_carries_per_source_health(client, auth_headers):
    """
    Source-health monitoring (2026-09) — the real fix for a real past
    incident: eTenders South Africa went silently dead for 3 weeks
    before a human noticed by hand (see CLAUDE.md), because the only
    "last run" signal available was /ingestion/jobs' `limit 20`
    GLOBAL feed, which a busier source can push a quiet one out of
    entirely. This checks every registered source now carries its OWN
    last-success/last-attempt/health fields, computed independently of
    that shared window, and that the three-way `health` state and its
    own stated threshold rule are internally consistent — not just
    present.
    """
    resp = client.get("/ingestion/sources/status", headers=auth_headers)
    body = resp.json()
    assert len(body["sources"]) >= 3

    for s in body["sources"]:
        assert s["health"] in ("healthy", "stale", "never_run")
        # health_threshold_hours must follow the documented rule
        # exactly: max(48, interval_hours * 3) — not just "some number".
        assert s["health_threshold_hours"] == max(48, s["interval_hours"] * 3)
        if s["health"] == "never_run":
            assert s["last_success_at"] is None
            assert s["hours_since_last_success"] is None
        else:
            # healthy/stale both require a real last_success_at, and
            # the state must actually match the threshold it was
            # computed against — not just be internally well-formed.
            assert s["last_success_at"] is not None
            assert s["hours_since_last_success"] is not None
            if s["health"] == "healthy":
                assert s["hours_since_last_success"] <= s["health_threshold_hours"]
            else:
                assert s["hours_since_last_success"] > s["health_threshold_hours"]

    # sam_gov has a real ingestion history in this dev database (used
    # throughout this project's own live-verification work) — assert
    # its health fields reflect a REAL prior run, not just a
    # plausible-looking shape.
    sam_gov_entry = next(s for s in body["sources"] if s["code"] == "sam_gov")
    assert sam_gov_entry["last_run_status"] in ("succeeded", "failed")
    assert sam_gov_entry["last_run_at"] is not None


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
