"""
Integration tests for the full MFA (TOTP) enrollment + login flow —
hits the real running API over HTTP, same discipline as every other
*_trigger/*_flow test in this suite (see conftest.py's own docstring).
Uses real pyotp TOTP generation against the real secret this project's
own /auth/mfa/setup returns, not a mocked authenticator.
"""

import pyotp
import pytest


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_login_without_mfa_returns_access_token_directly(client, new_tenant):
    resp = _login(client, new_tenant["email"], new_tenant["password"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] is not None
    assert body["mfa_required"] is False


def test_mfa_setup_returns_a_real_secret_and_provisioning_uri(client, auth_headers):
    resp = client.post("/auth/mfa/setup", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["secret"]
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    # A real TOTP secret — pyotp must be able to generate a code from it.
    code = pyotp.TOTP(body["secret"]).now()
    assert len(code) == 6 and code.isdigit()


def test_mfa_enable_with_wrong_code_fails(client, auth_headers):
    client.post("/auth/mfa/setup", headers=auth_headers)
    resp = client.post("/auth/mfa/enable", headers=auth_headers, json={"code": "000000"})
    assert resp.status_code == 401


def test_mfa_enable_without_setup_first_fails(client, auth_headers):
    resp = client.post("/auth/mfa/enable", headers=auth_headers, json={"code": "123456"})
    assert resp.status_code == 400


def _enroll_mfa(client, auth_headers):
    """Full real setup->enable flow, returns (secret, backup_codes)."""
    setup = client.post("/auth/mfa/setup", headers=auth_headers).json()
    secret = setup["secret"]
    code = pyotp.TOTP(secret).now()
    enable_resp = client.post("/auth/mfa/enable", headers=auth_headers, json={"code": code})
    assert enable_resp.status_code == 200, enable_resp.text
    return secret, enable_resp.json()["backup_codes"]


def test_mfa_enable_with_real_code_succeeds_and_returns_backup_codes(client, auth_headers):
    secret, backup_codes = _enroll_mfa(client, auth_headers)
    assert len(backup_codes) == 10
    assert len(set(backup_codes)) == 10


def test_mfa_setup_already_enabled_returns_409(client, auth_headers):
    _enroll_mfa(client, auth_headers)
    resp = client.post("/auth/mfa/setup", headers=auth_headers)
    assert resp.status_code == 409


def test_mfa_enable_already_enabled_returns_409(client, auth_headers):
    secret, _ = _enroll_mfa(client, auth_headers)
    code = pyotp.TOTP(secret).now()
    resp = client.post("/auth/mfa/enable", headers=auth_headers, json={"code": code})
    assert resp.status_code == 409


def test_get_me_reflects_mfa_enabled_flag(client, auth_headers):
    assert client.get("/auth/me", headers=auth_headers).json()["mfa_enabled"] is False
    _enroll_mfa(client, auth_headers)
    assert client.get("/auth/me", headers=auth_headers).json()["mfa_enabled"] is True


def test_login_after_enabling_mfa_requires_second_factor(client, auth_headers, new_tenant):
    _enroll_mfa(client, auth_headers)
    resp = _login(client, new_tenant["email"], new_tenant["password"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["mfa_required"] is True
    assert body["access_token"] is None
    assert body["mfa_challenge_token"]


def test_login_mfa_with_real_code_completes_login(client, auth_headers, new_tenant):
    secret, _ = _enroll_mfa(client, auth_headers)
    login_resp = _login(client, new_tenant["email"], new_tenant["password"])
    challenge_token = login_resp.json()["mfa_challenge_token"]

    code = pyotp.TOTP(secret).now()
    verify_resp = client.post("/auth/login/mfa", json={"mfa_challenge_token": challenge_token, "code": code})
    assert verify_resp.status_code == 200
    assert verify_resp.json()["access_token"]


def test_login_mfa_with_wrong_code_fails(client, auth_headers, new_tenant):
    _enroll_mfa(client, auth_headers)
    login_resp = _login(client, new_tenant["email"], new_tenant["password"])
    challenge_token = login_resp.json()["mfa_challenge_token"]

    verify_resp = client.post("/auth/login/mfa", json={"mfa_challenge_token": challenge_token, "code": "000000"})
    assert verify_resp.status_code == 401


def test_login_mfa_with_a_backup_code_completes_login_and_consumes_it(client, auth_headers, new_tenant):
    secret, backup_codes = _enroll_mfa(client, auth_headers)
    login_resp = _login(client, new_tenant["email"], new_tenant["password"])
    challenge_token = login_resp.json()["mfa_challenge_token"]
    used_code = backup_codes[0]

    verify_resp = client.post(
        "/auth/login/mfa", json={"mfa_challenge_token": challenge_token, "code": used_code}
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["access_token"]

    # The same backup code must never work a second time — a fresh
    # login + a fresh challenge token, but the SAME (now-consumed) code.
    second_login = _login(client, new_tenant["email"], new_tenant["password"])
    second_challenge = second_login.json()["mfa_challenge_token"]
    replay_resp = client.post(
        "/auth/login/mfa", json={"mfa_challenge_token": second_challenge, "code": used_code}
    )
    assert replay_resp.status_code == 401


def test_login_mfa_with_expired_or_garbage_challenge_token_fails(client):
    resp = client.post("/auth/login/mfa", json={"mfa_challenge_token": "not-a-real-token", "code": "123456"})
    assert resp.status_code == 401


def test_mfa_disable_requires_correct_password(client, auth_headers):
    _enroll_mfa(client, auth_headers)
    resp = client.post("/auth/mfa/disable", headers=auth_headers, json={"password": "totally-wrong-password"})
    assert resp.status_code == 401


def test_mfa_disable_with_correct_password_turns_off_mfa_and_login_no_longer_requires_it(
    client, auth_headers, new_tenant
):
    _enroll_mfa(client, auth_headers)
    disable_resp = client.post(
        "/auth/mfa/disable", headers=auth_headers, json={"password": new_tenant["password"]}
    )
    assert disable_resp.status_code == 200
    assert client.get("/auth/me", headers=auth_headers).json()["mfa_enabled"] is False

    login_resp = _login(client, new_tenant["email"], new_tenant["password"])
    body = login_resp.json()
    assert body["mfa_required"] is False
    assert body["access_token"] is not None


def test_a_user_can_re_enroll_mfa_after_disabling_it(client, auth_headers, new_tenant):
    """Real end-to-end: enroll, disable, enroll again with a fresh secret — must not be blocked by leftover state."""
    _enroll_mfa(client, auth_headers)
    client.post("/auth/mfa/disable", headers=auth_headers, json={"password": new_tenant["password"]})

    new_secret, new_backup_codes = _enroll_mfa(client, auth_headers)
    assert new_secret
    assert len(new_backup_codes) == 10
    assert client.get("/auth/me", headers=auth_headers).json()["mfa_enabled"] is True
