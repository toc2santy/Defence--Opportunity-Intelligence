"""
This is the one test in the whole suite that requires something I
could not verify myself: a real, live SAM.gov API key. It is
skipped automatically unless SAM_GOV_API_KEY is set in the
environment — so `pytest -v` continues to pass cleanly without it,
and this only runs when you deliberately opt in.

To actually run this test:
    export SAM_GOV_API_KEY=your_real_key
    pytest tests/test_sam_gov_live.py -v

This is the first genuine test of whether fetch_opportunities()
actually works against the real API — everything else in Phase 2
was verified against documented schema, not a live round-trip.

IMPORTANT — always run this file standalone, not as part of a full
`pytest -v` sweep with SAM_GOV_API_KEY set. test_rate_limiting.py
and test_naics_rotation_live.py both share this same endpoint's
5/hour budget — run any of these three files standalone, not
together, or one will get a 429 instead of a real result because
the rate limiter is correctly doing its job against traffic from
the same test-runner IP.
"""

import os
import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("SAM_GOV_API_KEY"),
    reason="SAM_GOV_API_KEY not set — skipping live SAM.gov API test. "
           "Set it and re-run to actually verify the live connector.",
)


def test_ingestion_endpoint_runs_against_real_sam_gov(client, auth_headers, db_cursor):
    """
    Note: this hits the REAL SAM.gov API through your API container,
    which must ALSO have SAM_GOV_API_KEY set in its own environment
    (docker-compose.yml / .env), not just in your shell running pytest.
    Uses a narrow NAICS code and short window to conserve your daily
    rate limit (personal keys: ~10 requests/day).
    """
    resp = client.post(
        "/ingestion/sam-gov/run",
        headers=auth_headers,
        json={"naics_codes": ["334511"], "days_back": 30},
    )
    assert resp.status_code == 200, f"ingestion failed: {resp.text}"
    body = resp.json()

    assert "job_id" in body
    assert "records_ingested" in body
    print(f"\nLive SAM.gov ingestion: {body['records_ingested']} records ingested")
    if body["errors"]:
        print(f"Errors encountered: {body['errors']}")
    if body["failures"]:
        print(f"Records that failed to normalize: {len(body['failures'])}")

    # A successful call should produce a job row we can look up
    jobs_resp = client.get("/ingestion/jobs", headers=auth_headers)
    assert jobs_resp.status_code == 200
    jobs = jobs_resp.json()
    assert any(j["id"] == body["job_id"] for j in jobs)

    # THE regression test for a real bug found while building the
    # eligibility feature: naics_code was never actually being
    # stored by this real ingestion path (only referenced in
    # unstructured evidence text), silently breaking Phase 3
    # matching for anything ingested after migration 004's one-time
    # backfill. If real records came back this run, at least one
    # must now genuinely have naics_code = '334511' as a real
    # column value, not just mentioned in a claim string.
    if body["records_ingested"] > 0:
        db_cursor.execute(
            "select count(*) as cnt from programmes where naics_code = %s and last_updated > now() - interval '5 minutes'",
            ("334511",),
        )
        row = db_cursor.fetchone()
        assert row["cnt"] > 0, (
            "ingestion reported records but none have naics_code stored as a real column — "
            "the regression is back"
        )
