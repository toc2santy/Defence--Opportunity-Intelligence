"""
Integration tests for the Forgot/Reset Password flow
(POST /auth/forgot-password, POST /auth/reset-password). Hits the
live API over HTTP.

The reset TOKEN itself is never testable from here: only its SHA-256
is stored (see db/migrations/023), and the raw value only ever
appears in the "email" app/email_sender.py sends — which, with no
SMTP account configured, means the API container's own stdout, not
anything this test process can read. That full round trip (request a
link, extract the token, reset, confirm the new password logs in,
confirm the old one no longer does, confirm the token can't be reused)
was verified manually via curl against the live container instead.
These tests cover what IS reachable purely over HTTP: status codes and
the no-enumeration response contract.
"""


def test_forgot_password_existing_email_returns_generic_message(client, new_tenant):
    resp = client.post("/auth/forgot-password", json={"email": new_tenant["email"]})
    assert resp.status_code == 200
    assert "reset link has been sent" in resp.json()["message"]


def test_forgot_password_nonexistent_email_returns_identical_message(client):
    resp = client.post("/auth/forgot-password", json={"email": "definitely-not-registered-xyz@example.com"})
    assert resp.status_code == 200
    assert "reset link has been sent" in resp.json()["message"]


def test_forgot_password_response_shape_never_reveals_existence(client, new_tenant):
    existing = client.post("/auth/forgot-password", json={"email": new_tenant["email"]}).json()
    missing = client.post("/auth/forgot-password", json={"email": "nope-not-registered-abc@example.com"}).json()
    assert existing == missing


def test_reset_password_rejects_invalid_token(client):
    resp = client.post("/auth/reset-password", json={"token": "not-a-real-token", "new_password": "aRealPassword123"})
    assert resp.status_code == 400


def test_reset_password_rejects_short_password(client):
    resp = client.post("/auth/reset-password", json={"token": "whatever", "new_password": "short"})
    assert resp.status_code == 422


def test_forgot_password_rejects_malformed_email(client):
    resp = client.post("/auth/forgot-password", json={"email": "not-an-email"})
    assert resp.status_code == 422
