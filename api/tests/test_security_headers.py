"""
Integration tests for the security response headers middleware
(2026-09) — a standard baseline (X-Content-Type-Options,
X-Frame-Options, Referrer-Policy, HSTS, CSP) this API had none of
before now, applied to every response via one middleware.
"""


def test_security_headers_present_on_a_real_response(client):
    resp = client.get("/healthz")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "max-age=31536000" in resp.headers.get("strict-transport-security", "")


def test_csp_present_on_a_normal_json_route(client):
    resp = client.get("/healthz")
    assert "default-src 'none'" in resp.headers.get("content-security-policy", "")


def test_csp_absent_on_docs_so_swagger_ui_still_renders(client):
    resp = client.get("/docs")
    assert "content-security-policy" not in {k.lower() for k in resp.headers.keys()}
    # The other headers still apply even on /docs — only CSP is skipped there.
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_security_headers_present_even_on_an_error_response(client):
    resp = client.get("/platform-admin/users/00000000-0000-0000-0000-000000000000/activity")
    assert resp.status_code in (401, 403)
    assert resp.headers.get("x-content-type-options") == "nosniff"
