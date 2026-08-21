"""
Shared fixtures for the smoke test suite.

These tests hit the REAL running stack over HTTP (localhost:8000) —
they are not unit tests against the code in isolation, and they
do not mock the database. That's deliberate: the whole point of
this suite is to catch the class of bug we just spent a dozen
rounds finding by hand (missing DB grants, a jsonb serialization
bug, a SQLAlchemy `::` cast parsing quirk) — none of which a
mocked-DB unit test would have caught, because all of them only
showed up when real SQL hit a real Postgres instance.

Prerequisite: `docker compose up` must already be running.
"""

import uuid

import httpx
import psycopg2
import psycopg2.extras
import pytest

BASE_URL = "http://localhost:8000"

# Direct DB access, for test SETUP only (seeding controlled fixture
# data) — never used to assert on results, since the whole point of
# this suite is asserting through the real API, the way a real user
# would experience it. Connects as postgres (bypasses RLS), which is
# fine here: programmes/organizations/evidence are shared reference
# tables with no RLS by design, so there's nothing to bypass.
DB_DSN = "postgresql://postgres:postgres@localhost:5432/doi"


def unique_email() -> str:
    # A fresh email per test run avoids colliding with tenants
    # created in earlier manual testing or earlier test runs —
    # this suite is designed to be re-run repeatedly without
    # needing to wipe the database first.
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(scope="session")
def client():
    # 10s was fine originally, when /ingestion/sam-gov/run failed
    # fast (400) with no key configured. Once a real SAM_GOV_API_KEY
    # is set (as it now is in this project's own dev environment),
    # every call genuinely hits the live SAM.gov API — up to 3
    # sequential NAICS-code fetches, each internally allowed up to
    # 30s (see app/sam_gov_ingestion.py's fetch_opportunities) — so
    # a short client-side timeout here caused a real ReadTimeout on
    # test_rate_limiting.py, not a server-side bug. 120s comfortably
    # covers the worst real case without meaningfully slowing down
    # the many tests that fail fast and never approach it.
    with httpx.Client(base_url=BASE_URL, timeout=120.0) as c:
        yield c


@pytest.fixture
def new_tenant(client):
    """Signs up a brand-new tenant + admin user, returns (token, email, company_name)."""
    email = unique_email()
    company_name = f"Test Company {uuid.uuid4().hex[:6]}"
    password = "a-real-password-123"

    resp = client.post(
        "/auth/signup",
        json={"company_name": company_name, "email": email, "password": password},
    )
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    token = resp.json()["access_token"]
    return {"token": token, "email": email, "password": password, "company_name": company_name}


@pytest.fixture
def auth_headers(new_tenant):
    return {"Authorization": f"Bearer {new_tenant['token']}"}


@pytest.fixture
def db_cursor():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    yield cur
    cur.close()
    conn.close()
