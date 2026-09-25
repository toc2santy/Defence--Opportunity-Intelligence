"""
A minimal, real, spec-compliant OIDC provider — used ONLY to prove
app/oidc.py's login flow against a genuine authorization-code
exchange + JWKS-verified RS256 id_token, the same real mechanics any
actual provider (Google, Microsoft, Keycloak) uses. This is not a
mock of this project's own code — it's a second, independent HTTP
service implementing the actual OIDC endpoints a client is required
to call, so testing against it exercises the real network+crypto path
(discovery, code exchange, JWKS fetch, RS256 signature verification),
not a stubbed-out shortcut around any of it.

Deliberately skips the one thing a real provider has that matters to
a HUMAN, not to this test — an actual login/consent UI. /authorize
here immediately redirects back with a code for whatever
fake_email/fake_name query params were passed in, since nothing in
this project's own code cares how a user authenticated AT the
provider, only that the provider's response is genuine and verifiable.

Run standalone: uvicorn tests.fake_oidc_provider:app --port 9100
"""

import base64
import time
import uuid
from urllib.parse import parse_qsl

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse

app = FastAPI(title="Fake OIDC Provider (test-only)")

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_kid = "fake-test-key-1"

_ISSUED_CODES: dict[str, dict] = {}  # code -> {email, name, nonce, redirect_uri, client_id}


def _b64url_uint(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@app.get("/.well-known/openid-configuration")
async def discovery(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "jwks_uri": f"{base}/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get("/jwks.json")
async def jwks():
    pub = _private_key.public_key().public_numbers()
    return {
        "keys": [{
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": _kid,
            "n": _b64url_uint(pub.n),
            "e": _b64url_uint(pub.e),
        }]
    }


@app.get("/authorize")
async def authorize(request: Request):
    """No real login screen — auto-approves using fake_email/fake_name query params, matching this module's own docstring."""
    params = request.query_params
    code = secrets_token()
    _ISSUED_CODES[code] = {
        "email": params.get("fake_email", "fake-oidc-user@example.com"),
        "name": params.get("fake_name", "Fake OIDC User"),
        # Lets tests exercise app/oidc.py's own email_verified handling
        # for all three real-world shapes: "true" (Google, always),
        # "false" (an explicit no — the one real reject case), and
        # "omit" (the claim absent entirely — Microsoft's real
        # behavior for a personal outlook.com/hotmail.com account,
        # found live 2026-09; must NOT be treated as false).
        "email_verified": params.get("fake_email_verified", "true"),
        "nonce": params.get("nonce", ""),
        "redirect_uri": params["redirect_uri"],
        "client_id": params["client_id"],
        "issued_at": time.time(),
    }
    redirect_uri = params["redirect_uri"]
    state = params.get("state", "")
    return RedirectResponse(f"{redirect_uri}?code={code}&state={state}")


def secrets_token() -> str:
    return uuid.uuid4().hex


@app.post("/token")
async def token(request: Request):
    # Manual parsing rather than Starlette's request.form() — this
    # app's real token exchange (app/oidc.py's exchange_code_for_tokens)
    # always sends application/x-www-form-urlencoded, and pulling in
    # python-multipart (which newer Starlette versions require even
    # for urlencoded bodies) as a dependency just for this test-only
    # provider isn't worth it.
    raw_body = await request.body()
    form = dict(parse_qsl(raw_body.decode()))
    code = form.get("code")
    entry = _ISSUED_CODES.pop(code, None)
    if entry is None:
        return JSONResponse({"error": "invalid_grant", "error_description": "unknown or already-used code"}, status_code=400)

    if form.get("client_id") != entry["client_id"] or form.get("redirect_uri") != entry["redirect_uri"]:
        return JSONResponse({"error": "invalid_grant", "error_description": "client_id/redirect_uri mismatch"}, status_code=400)
    if not form.get("code_verifier"):
        return JSONResponse({"error": "invalid_grant", "error_description": "missing PKCE code_verifier"}, status_code=400)

    base = str(request.base_url).rstrip("/")
    now = int(time.time())
    claims = {
        "iss": base,
        "aud": entry["client_id"],
        "sub": f"fake-subject-{entry['email']}",
        "email": entry["email"],
        "name": entry["name"],
        "nonce": entry["nonce"],
        "iat": now,
        "exp": now + 300,
    }
    # "omit" means genuinely leave the key out of the token — not the
    # same as sending false — matching Microsoft's real personal-
    # account behavior this claim is meant to simulate.
    if entry["email_verified"] != "omit":
        claims["email_verified"] = entry["email_verified"].lower() != "false"
    id_token = jwt.encode(
        claims,
        _private_key,
        algorithm="RS256",
        headers={"kid": _kid},
    )
    return {"access_token": uuid.uuid4().hex, "token_type": "Bearer", "id_token": id_token, "expires_in": 300}
