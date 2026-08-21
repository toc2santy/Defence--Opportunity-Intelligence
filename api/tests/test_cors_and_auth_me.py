"""
Tests for the two backend additions that unblock real frontend
wiring: CORS (so a browser on a different origin is actually
allowed to call this API at all) and /auth/me (so the frontend has
something to greet the user with beyond a bare JWT).
"""

import os

# Same honest limitation as INGESTION_RATE_LIMIT in
# test_rate_limiting.py: pytest runs on the host, FRONTEND_ORIGIN is
# read inside the API container, and they're separate processes
# with separate environments. If you've set a non-default
# FRONTEND_ORIGIN in .env (very likely — a port conflict on 5500 is
# common enough that this project's own real dev environment ended
# up on 5501), export the SAME value here before running pytest:
#   export TEST_FRONTEND_ORIGIN=http://localhost:5501
EXPECTED_ORIGIN = os.environ.get("TEST_FRONTEND_ORIGIN", "http://localhost:5500")


def test_cors_headers_present_for_allowed_origin(client):
    resp = client.get("/healthz", headers={"Origin": EXPECTED_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == EXPECTED_ORIGIN, (
        f"expected CORS to allow {EXPECTED_ORIGIN}, but it didn't. If you've set a "
        f"custom FRONTEND_ORIGIN in .env, set TEST_FRONTEND_ORIGIN to match before "
        f"running pytest (see this file's top comment)."
    )


def test_cors_headers_absent_for_disallowed_origin(client):
    resp = client.get("/healthz", headers={"Origin": "http://evil-example.com"})
    assert resp.status_code == 200  # the request itself still succeeds server-side...
    # ...but no CORS header permits a BROWSER to read the response
    # from that origin. This is what actually protects a real user —
    # the enforcement happens in the browser, not the server, so
    # this header's absence is the meaningful assertion here.
    assert resp.headers.get("access-control-allow-origin") != "http://evil-example.com"


def test_auth_me_returns_real_user_info(client, auth_headers, new_tenant):
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == new_tenant["email"]
    assert body["role"] == "admin"  # signup always creates the first user as admin
    assert body["company_name"] == new_tenant["company_name"]
    assert body["tenant_id"]
    assert "plan" in body


def test_auth_me_requires_authentication(client):
    resp = client.get("/auth/me")
    assert resp.status_code in (401, 403)


def test_auth_me_only_ever_returns_the_caller_s_own_info(client):
    # two separate tenants — confirm each only ever sees their own
    # email/company through /auth/me, never the other's
    import uuid

    email_a = f"me-test-a-{uuid.uuid4().hex[:8]}@example.com"
    signup_a = client.post(
        "/auth/signup",
        json={"company_name": "Company A", "email": email_a, "password": "a-real-password-123"},
    )
    token_a = signup_a.json()["access_token"]

    email_b = f"me-test-b-{uuid.uuid4().hex[:8]}@example.com"
    signup_b = client.post(
        "/auth/signup",
        json={"company_name": "Company B", "email": email_b, "password": "a-real-password-456"},
    )
    token_b = signup_b.json()["access_token"]

    me_a = client.get("/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    me_b = client.get("/auth/me", headers={"Authorization": f"Bearer {token_b}"})

    assert me_a.json()["email"] == email_a
    assert me_a.json()["company_name"] == "Company A"
    assert me_b.json()["email"] == email_b
    assert me_b.json()["company_name"] == "Company B"
