"""
Integration tests for /ingestion/uk-ft/run — the manual trigger for
the second real ingestion source. Requires an admin role and shares
the same rate limit as SAM.gov's ingestion trigger (both go through
INGESTION_RATE_LIMIT), so avoid running this alongside
test_rate_limiting.py or test_sam_gov_live.py in one sweep for the
same reason documented in those files.

test_uk_ft_trigger_response_shape hits the REAL live UK government
API — no credential is needed for this source, so nothing gates it
the way a missing SAM_GOV_API_KEY gates the SAM.gov live test.
Deliberately opt-in anyway, via RUN_LIVE_UK_TEST, out of the same
respect for not hammering a live government service on every
routine `pytest -v` run that motivated SAM.gov's key-based gate.

To run:
    RUN_LIVE_UK_TEST=1 pytest tests/test_uk_ft_trigger.py -v
"""

import os

import pytest


def test_uk_ft_trigger_requires_admin(client):
    resp = client.post("/ingestion/uk-ft/run", json={})
    assert resp.status_code in (401, 403)


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_UK_TEST"),
    reason="Set RUN_LIVE_UK_TEST=1 to run this — it hits the real live UK government API.",
)
def test_uk_ft_trigger_response_shape(client, auth_headers):
    """
    Doesn't assert on records_ingested count — this genuinely hits
    the live UK government API (no key needed, unlike SAM.gov), and
    how many defense-relevant notices exist in the last 7 days is
    real, variable data, not something to hardcode an expectation
    against. What's actually testable is the response SHAPE.
    """
    resp = client.post("/ingestion/uk-ft/run", headers=auth_headers, json={"days_back": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert "records_ingested" in body
    assert "date_range" in body
    assert "api_key_status" not in body  # this source has no credential, unlike SAM.gov's response
