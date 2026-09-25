"""
Integration tests for SSO via OIDC (2026-09): GET /auth/oidc/{provider}
/login + /callback, POST /auth/oidc/complete-signup.

Runs the REAL authorization-code + PKCE + JWKS-verified-RS256 flow
against api/tests/fake_oidc_provider.py (a genuine second OIDC
service, not a mock of this project's own code — see that module's
own docstring), wired into docker-compose.test.yml as the
`fake-oidc-test` service, provider slug "testprovider".

One networking wrinkle worth explaining: the real /auth/oidc/{provider}
/login route builds its redirect_uri from API_BASE_URL, which for the
isolated test stack is the docker-internal address
http://api-test:8000/... (see docker-compose.test.yml) — not reachable
from this host-side pytest process. So these tests call
fake-oidc-test's own /authorize endpoint directly (mapped to the host
at localhost:9100) with that same internal redirect_uri as a query
param — satisfying the fake provider's own bookkeeping — then extract
just the `code`/`state` it returns and hand THOSE to the real
/auth/oidc/testprovider/callback route via ITS host-mapped address
(localhost:8001). The actual code-for-token exchange still happens
server-side, api-test container to fake-oidc-test container, over the
real docker network — this host-side substitution only replaces which
URL a human/browser would have been redirected through, not any part
of the security-relevant exchange itself.
"""

import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
import pytest

FAKE_OIDC_BASE_URL = os.environ.get("TEST_FAKE_OIDC_BASE_URL", "http://localhost:9100")
# The internal address the real callback route will reconstruct
# server-side (API_BASE_URL in docker-compose.test.yml) — must match
# exactly, or the fake provider's own redirect_uri check (mirroring
# what a real provider does) rejects the exchange.
INTERNAL_REDIRECT_URI = os.environ.get("TEST_OIDC_INTERNAL_REDIRECT_URI", "http://api-test:8000/auth/oidc/testprovider/callback")


def _jwt_secret() -> str:
    env_secret = os.environ.get("JWT_SECRET")
    if env_secret:
        return env_secret
    # Falls back to reading it straight out of .env — docker compose
    # auto-loads that file for container env vars, but a bare host
    # venv running pytest doesn't, and this suite genuinely needs the
    # SAME secret the API container signs/verifies with to build a
    # valid state token itself.
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    with open(env_path) as f:
        content = f.read()
    m = re.search(r"^JWT_SECRET=(.+)$", content, re.M)
    if not m:
        pytest.skip("JWT_SECRET not found in environment or .env — cannot build a valid OIDC state token")
    return m.group(1).strip()


def _make_pkce():
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return code_verifier, code_challenge


def _make_state(provider, code_verifier, nonce):
    return jwt.encode(
        {
            "provider": provider, "code_verifier": code_verifier, "nonce": nonce,
            "redirect_after": None,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
            "purpose": "oidc_state",
        },
        _jwt_secret(),
        algorithm="HS256",
    )


def _run_authorize(client, *, fake_email, fake_name="Fake User", fake_email_verified="true", nonce=None, state=None):
    """Hits the fake provider's /authorize directly, returns (code, state) extracted from its redirect."""
    code_verifier, code_challenge = _make_pkce()
    nonce = nonce or secrets.token_urlsafe(16)
    state = state or _make_state("testprovider", code_verifier, nonce)

    resp = client.get(
        f"{FAKE_OIDC_BASE_URL}/authorize",
        params={
            "response_type": "code",
            "client_id": "test-client-id",
            "redirect_uri": INTERNAL_REDIRECT_URI,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "fake_email": fake_email,
            "fake_name": fake_name,
            "fake_email_verified": fake_email_verified,
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307), f"fake provider /authorize did not redirect: {resp.status_code} {resp.text}"
    q = parse_qs(urlparse(resp.headers["location"]).query)
    return q["code"][0], q["state"][0]


def _hit_callback(client, code, state):
    return client.get(
        "/auth/oidc/testprovider/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )


def _query_param(location_url: str, name: str):
    return parse_qs(urlparse(location_url).query).get(name, [None])[0]


def test_login_redirects_to_the_providers_real_authorization_endpoint(client):
    resp = client.get("/auth/oidc/testprovider/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{INTERNAL_REDIRECT_URI.rsplit('/auth/', 1)[0]}".replace("api-test", "fake-oidc-test")) or "authorize" in location
    assert "code_challenge=" in location
    assert "state=" in location
    assert "nonce=" in location


def test_login_rejects_an_unconfigured_provider(client):
    resp = client.get("/auth/oidc/not-a-real-provider/login", follow_redirects=False)
    assert resp.status_code == 400


def test_callback_rejects_a_tampered_state_token(client):
    resp = _hit_callback(client, "whatever-code", "not-a-real-jwt")
    assert resp.status_code == 302
    assert "oidc_error" in resp.headers["location"]


def test_callback_rejects_a_state_whose_provider_does_not_match_the_url(client):
    code_verifier, _ = _make_pkce()
    mismatched_state = _make_state("some-other-provider", code_verifier, "nonce-x")
    resp = _hit_callback(client, "whatever-code", mismatched_state)
    assert resp.status_code == 302
    assert "does not match the callback provider" in _query_param(resp.headers["location"], "oidc_error")


def test_callback_rejects_a_nonce_mismatch(client):
    code_verifier, code_challenge = _make_pkce()
    state = _make_state("testprovider", code_verifier, "nonce-in-state")
    # /authorize is told a DIFFERENT nonce than what's embedded in state
    resp = client.get(
        f"{FAKE_OIDC_BASE_URL}/authorize",
        params={
            "response_type": "code", "client_id": "test-client-id",
            "redirect_uri": INTERNAL_REDIRECT_URI, "scope": "openid email profile",
            "state": state, "nonce": "nonce-actually-used", "code_challenge": code_challenge,
            "code_challenge_method": "S256", "fake_email": "noncecheck@example.com",
        },
        follow_redirects=False,
    )
    q = parse_qs(urlparse(resp.headers["location"]).query)
    code, ret_state = q["code"][0], q["state"][0]

    cb = _hit_callback(client, code, ret_state)
    assert cb.status_code == 302
    assert "nonce mismatch" in _query_param(cb.headers["location"], "oidc_error")


def test_callback_rejects_an_unverified_email_claim(client):
    code, state = _run_authorize(client, fake_email="unverified-oidc@example.com", fake_email_verified="false")
    resp = _hit_callback(client, code, state)
    assert resp.status_code == 302
    assert "unverified" in resp.headers["location"]


def test_new_email_gets_a_signup_token_not_an_access_token(client):
    email = f"oidc-new-{secrets.token_hex(6)}@example.com"
    code, state = _run_authorize(client, fake_email=email, fake_name="New OIDC User")
    resp = _hit_callback(client, code, state)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert _query_param(location, "oidc_token") is None
    signup_token = _query_param(location, "oidc_signup_token")
    assert signup_token is not None
    assert _query_param(location, "oidc_email") == email


def test_complete_signup_creates_a_real_verified_account(client):
    email = f"oidc-complete-{secrets.token_hex(6)}@example.com"
    code, state = _run_authorize(client, fake_email=email, fake_name="Complete Signup User")
    cb = _hit_callback(client, code, state)
    signup_token = _query_param(cb.headers["location"], "oidc_signup_token")

    resp = client.post("/auth/oidc/complete-signup", json={"signup_token": signup_token, "company_name": "OIDC Test Co"})
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == email
    assert body["email_verified"] is True
    assert body["company_name"] == "OIDC Test Co"


def test_a_returning_linked_identity_gets_a_real_access_token_not_a_signup_token(client):
    email = f"oidc-returning-{secrets.token_hex(6)}@example.com"
    code, state = _run_authorize(client, fake_email=email, fake_name="Returning User")
    cb = _hit_callback(client, code, state)
    signup_token = _query_param(cb.headers["location"], "oidc_signup_token")
    signup_resp = client.post("/auth/oidc/complete-signup", json={"signup_token": signup_token, "company_name": "Returning Co"})
    original_user_id = jwt.decode(signup_resp.json()["access_token"], options={"verify_signature": False})["sub"]

    # Sign in again with the SAME email via SSO.
    code2, state2 = _run_authorize(client, fake_email=email, fake_name="Returning User")
    cb2 = _hit_callback(client, code2, state2)
    assert cb2.status_code == 302
    access_token = _query_param(cb2.headers["location"], "oidc_token")
    assert access_token is not None
    assert _query_param(cb2.headers["location"], "oidc_signup_token") is None

    returning_user_id = jwt.decode(access_token, options={"verify_signature": False})["sub"]
    assert returning_user_id == original_user_id


def test_an_existing_password_account_auto_links_on_first_sso_login_with_the_same_email(client, new_tenant):
    code, state = _run_authorize(client, fake_email=new_tenant["email"], fake_name="Auto Link")
    resp = _hit_callback(client, code, state)
    assert resp.status_code == 302
    access_token = _query_param(resp.headers["location"], "oidc_token")
    assert access_token is not None, f"expected a direct login, got: {resp.headers['location']}"

    linked_user_id = jwt.decode(access_token, options={"verify_signature": False})["sub"]

    password_login = client.post("/auth/login", json={"email": new_tenant["email"], "password": new_tenant["password"]})
    assert password_login.status_code == 200
    password_user_id = jwt.decode(password_login.json()["access_token"], options={"verify_signature": False})["sub"]

    assert linked_user_id == password_user_id


def test_complete_signup_rejects_a_reused_or_forged_token(client):
    resp = client.post("/auth/oidc/complete-signup", json={"signup_token": "not-a-real-token", "company_name": "Whatever Co"})
    assert resp.status_code == 400
