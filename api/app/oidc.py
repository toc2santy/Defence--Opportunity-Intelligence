"""
SSO via OIDC (2026-09) — pure logic + HTTP calls to a provider, no DB
access (main.py's routes own the oidc_identities/users queries), same
separation as app/mfa.py.

Config-driven, not hardcoded to Google/Microsoft specifically: any
standard OIDC provider works by setting three env vars per provider
slug — OIDC_{SLUG}_CLIENT_ID, OIDC_{SLUG}_CLIENT_SECRET,
OIDC_{SLUG}_ISSUER. Google is https://accounts.google.com; Microsoft
(any-tenant) is https://login.microsoftonline.com/common/v2.0; a
self-hosted provider (Keycloak, Dex, this project's own test-double
used in tests/) is whatever URL it serves its own
/.well-known/openid-configuration from. discover_provider() reads
that document itself rather than hardcoding each provider's
authorization/token/jwks endpoints, so nothing here breaks if a
provider ever moves them.

Security properties, all standard OIDC/OAuth 2.1 practice, not
project-specific inventions:
  - PKCE (S256) even though this is a confidential client (the app
    holds a real client_secret) — OAuth 2.1 recommends it
    unconditionally now, and it costs nothing extra here.
  - `state` carries the PKCE code_verifier + nonce + provider slug as
    a short-lived, SERVER-SIGNED JWT (reusing JWT_SECRET) rather than
    server-side session storage — this app has no session store
    anywhere else (every other flow is already stateless JWTs), and a
    signed state means nothing needs cleaning up if a login is
    abandoned mid-flow.
  - `nonce` is checked against the id_token's own `nonce` claim,
    the OIDC-specific replay protection `state` alone doesn't cover.
  - id_token signature is verified against the provider's own JWKS
    (fetched fresh, not pinned — a provider rotating its signing key
    is normal and expected), and `iss`/`aud`/`exp` are all checked
    before any claim in the token is trusted for anything.
"""

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
from jwt import PyJWKClient

STATE_TOKEN_MINUTES = 10


class OidcConfigError(Exception):
    """Raised when a requested provider slug has no client_id/secret/issuer configured."""


class OidcVerificationError(Exception):
    """Raised when a callback's code exchange or id_token verification fails — never trust a partially-verified token."""


def _env(provider: str, key: str) -> Optional[str]:
    return os.environ.get(f"OIDC_{provider.upper()}_{key}")


def get_provider_config(provider: str) -> dict:
    client_id = _env(provider, "CLIENT_ID")
    client_secret = _env(provider, "CLIENT_SECRET")
    issuer = _env(provider, "ISSUER")
    if not all((client_id, client_secret, issuer)):
        raise OidcConfigError(f"SSO provider '{provider}' is not configured (missing OIDC_{provider.upper()}_CLIENT_ID/CLIENT_SECRET/ISSUER)")
    return {"client_id": client_id, "client_secret": client_secret, "issuer": issuer.rstrip("/")}


async def discover_provider(issuer: str) -> dict:
    """Fetches the provider's own /.well-known/openid-configuration — never hardcode a provider's endpoints, they're free to change them."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{issuer}/.well-known/openid-configuration")
        resp.raise_for_status()
        return resp.json()


def make_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) — S256 per RFC 7636."""
    import base64
    import hashlib

    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return code_verifier, code_challenge


def make_state_token(jwt_secret: str, provider: str, code_verifier: str, nonce: str, redirect_after: Optional[str]) -> str:
    payload = {
        "provider": provider,
        "code_verifier": code_verifier,
        "nonce": nonce,
        "redirect_after": redirect_after,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=STATE_TOKEN_MINUTES),
        "purpose": "oidc_state",
    }
    return jwt.encode(payload, jwt_secret, algorithm="HS256")


def decode_state_token(jwt_secret: str, state: str) -> dict:
    try:
        payload = jwt.decode(state, jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise OidcVerificationError(f"Invalid or expired SSO state — please try signing in again ({e})")
    if payload.get("purpose") != "oidc_state":
        raise OidcVerificationError("Invalid SSO state token")
    return payload


async def exchange_code_for_tokens(token_endpoint: str, client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise OidcVerificationError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _issuer_matches(actual_issuer: str, expected_issuer_template: str) -> bool:
    """
    Usually an exact match (Google, or any single-tenant provider).
    Microsoft's multi-tenant "common"/"organizations"/"consumers"
    endpoints are the documented exception: their OWN discovery
    document's `issuer` field literally contains the placeholder
    string `{tenantid}` (e.g.
    "https://login.microsoftonline.com/{tenantid}/v2.0"), which a
    real id_token's `iss` claim replaces with whichever real tenant
    GUID the signer-upper actually authenticated against — an exact
    string match against the un-substituted template can never pass,
    by Microsoft's own design, not a bug in this app. Found live
    (2026-09) testing "Continue with Microsoft" against a real
    account. Falls back to a regex built from the template with
    `{tenantid}` replaced by a GUID pattern — still a real,
    structural verification, not a blanket "any issuer" bypass.
    """
    if "{tenantid}" not in expected_issuer_template:
        return actual_issuer == expected_issuer_template
    pattern = "^" + re.escape(expected_issuer_template).replace(re.escape("{tenantid}"), r"[0-9a-fA-F-]{36}") + "$"
    return re.match(pattern, actual_issuer) is not None


def verify_id_token(id_token: str, jwks_uri: str, issuer: str, client_id: str, expected_nonce: str) -> dict:
    """
    Verifies signature (against the provider's own live JWKS),
    issuer, audience, expiry, and nonce — every check OIDC's spec
    requires before a single claim in the token can be trusted.
    PyJWKClient caches the JWKS response itself; a provider rotating
    its signing key mid-cache is the one scenario this could miss,
    the same acceptable window any JWKS-caching client has.

    Issuer is checked manually (`_issuer_matches`, above) rather than
    via PyJWT's own built-in `issuer=` exact-match, specifically for
    Microsoft's multi-tenant `{tenantid}` template — see that
    function's own docstring.
    """
    jwk_client = PyJWKClient(jwks_uri)
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)
    try:
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
        )
    except jwt.PyJWTError as e:
        raise OidcVerificationError(f"id_token verification failed: {e}")

    if not _issuer_matches(claims.get("iss", ""), issuer):
        raise OidcVerificationError(f"id_token issuer mismatch: got {claims.get('iss')!r}, expected to match {issuer!r}")

    if claims.get("nonce") != expected_nonce:
        raise OidcVerificationError("id_token nonce mismatch — possible replay")

    if not claims.get("email"):
        raise OidcVerificationError("Provider did not return an email claim")
    # `email_verified` is an OPTIONAL OIDC claim (spec section 5.1) —
    # a provider omitting it is NOT the same statement as a provider
    # explicitly sending `false`, and treating "didn't say" as
    # "unverified" (the original `.get(..., False)` did) turns out to
    # reject entirely legitimate providers. Found live (2026-09):
    # Microsoft's real id_token for a personal (outlook.com/hotmail.com)
    # account omits this claim altogether — Google always sends it,
    # Microsoft does not, and Microsoft's own account creation already
    # requires a verified email before an account can exist at all.
    # Only an EXPLICIT `false` is trusted as a real "no" here; absent
    # or `true` both pass.
    if claims.get("email_verified") is False:
        raise OidcVerificationError("Provider reports this email as unverified — cannot use it to sign in")

    return claims
