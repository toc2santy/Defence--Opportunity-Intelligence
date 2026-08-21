"""
Proves the rate-limiting mechanism actually works — a 429 response
after exceeding the real configured limit, not just that a decorator
is present in the source. Uses /ingestion/sam-gov/run's real 5/hour
limit rather than an artificial test-only endpoint, since that
endpoint genuinely warrants a strict limit on its own merits (no
reason to allow more frequent triggering than SAM.gov's own ~10/day
quota supports).

Deliberately robust to the limiter's state persisting across
repeated pytest runs within the same hour (see the test itself for
why that matters — it's not a hypothetical, it's how this exact
suite has actually been run many times over during this project).
"""


# Note: this test assumes the documented default INGESTION_RATE_LIMIT
# (5/hour). There's no reliable way to check the *container's* actual
# configured value from here — pytest runs on the host, the limit is
# read inside the API container, and they're separate processes with
# separate environments. If you've deliberately widened
# INGESTION_RATE_LIMIT in docker-compose.yml/.env for local
# convenience, this test may fail — that's expected, not a bug.


def test_ingestion_endpoint_eventually_rate_limits(client, auth_headers):
    """
    Deliberately does NOT assume a fresh rate-limit window. The
    limiter's state lives in the API process's memory and persists
    across pytest runs within the container's uptime — running this
    suite twice inside the same hour (routine during normal
    development) means this endpoint's budget may already be
    partially or fully consumed before this test even starts. So
    instead of asserting "the first 5 succeed, the 6th fails," this
    proves the mechanism itself: within a bounded number of calls we
    definitely see a 429, and once we do, it stays a 429 — true
    whether we're starting from a fresh budget or an exhausted one.
    """
    saw_429 = False
    for _ in range(6):
        resp = client.post(
            "/ingestion/sam-gov/run",
            headers=auth_headers,
            json={"naics_codes": ["334511"], "days_back": 1},
        )
        if resp.status_code == 429:
            saw_429 = True
            break

    assert saw_429, "expected to hit the rate limit within 6 calls, but never got a 429"

    # once limited, the very next call should still be limited too —
    # confirms this is a real enforced window, not a one-off fluke
    follow_up = client.post(
        "/ingestion/sam-gov/run",
        headers=auth_headers,
        json={"naics_codes": ["334511"], "days_back": 1},
    )
    assert follow_up.status_code == 429
    assert len(follow_up.text) > 0  # a real body, not an empty response
