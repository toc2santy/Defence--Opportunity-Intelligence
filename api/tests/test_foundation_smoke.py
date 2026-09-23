"""
Phase 0 smoke tests.

Run with the stack already up:
    docker compose up -d
    cd api && pytest -v

This suite exists to replay, automatically, the exact sequence we
verified by hand: health check, signup, duplicate-signup rejection,
login (success and failure), authenticated product creation, and —
the one that matters most — proof that a second tenant genuinely
cannot see a first tenant's data. If any of these regress after a
future change, this suite catches it in seconds instead of another
long manual debugging round.
"""

import httpx


# ---------------------------------------------------------------
# Basic liveness
# ---------------------------------------------------------------
def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------
# Signup
# ---------------------------------------------------------------
def test_signup_returns_token(new_tenant):
    assert new_tenant["token"]
    assert len(new_tenant["token"]) > 20  # a JWT is never this short


def test_signup_duplicate_email_is_rejected(client, new_tenant):
    resp = client.post(
        "/auth/signup",
        json={
            "company_name": "Another Company", "full_name": "Test Admin",
            "email": new_tenant["email"],  # reuse the same email deliberately
            "password": "a-different-password-1",
        },
    )
    assert resp.status_code == 409


def test_signup_rejects_short_password(client):
    resp = client.post(
        "/auth/signup",
        json={
            "company_name": "Short Password Co", "full_name": "Test Admin",
            "email": "shortpw@example.com",
            "password": "tooshort",  # under 10 chars
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------
# Login
# ---------------------------------------------------------------
def test_login_with_correct_credentials(client, new_tenant):
    resp = client.post(
        "/auth/login",
        json={"email": new_tenant["email"], "password": new_tenant["password"]},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_with_wrong_password_is_rejected(client, new_tenant):
    resp = client.post(
        "/auth/login",
        json={"email": new_tenant["email"], "password": "definitely-not-the-password"},
    )
    assert resp.status_code == 401


def test_login_with_unknown_email_is_rejected(client):
    resp = client.post(
        "/auth/login",
        json={"email": "nobody-signed-up-with-this@example.com", "password": "whatever-password"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------
# Authenticated product create/list
# ---------------------------------------------------------------
def test_requires_auth_header(client):
    resp = client.get("/products")
    assert resp.status_code in (401, 403)  # FastAPI's HTTPBearer returns 403 when no header is sent


def test_create_and_list_product(client, auth_headers):
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Secure UAV Data Link", "description": "Test product", "trl": 7},
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    list_resp = client.get("/products", headers=auth_headers)
    assert list_resp.status_code == 200
    products = list_resp.json()
    assert any(p["id"] == product_id for p in products)


# ---------------------------------------------------------------
# Tenant isolation — the check that matters most. This is the
# automated version of the two-tenant curl test we ran by hand.
# ---------------------------------------------------------------
def test_tenant_cannot_see_another_tenants_products(client, auth_headers):
    # tenant A creates a product
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Tenant A Only Product", "description": "Should not leak", "trl": 6},
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    # a completely separate tenant B signs up fresh
    import uuid
    email_b = f"tenant-b-{uuid.uuid4().hex[:10]}@example.com"
    signup_b = client.post(
        "/auth/signup",
        json={"company_name": "Tenant B Co", "full_name": "Test Admin", "email": email_b, "password": "another-real-password-1"},
    )
    assert signup_b.status_code == 201
    token_b = signup_b.json()["access_token"]

    # tenant B lists products — must NOT see tenant A's product.
    # This is enforced by Postgres Row-Level Security, not by any
    # filtering logic in the route itself.
    list_b = client.get("/products", headers={"Authorization": f"Bearer {token_b}"})
    assert list_b.status_code == 200
    product_ids_visible_to_b = [p["id"] for p in list_b.json()]
    assert product_id not in product_ids_visible_to_b, (
        "TENANT ISOLATION FAILURE: tenant B could see tenant A's product. "
        "This means Row-Level Security is not working as designed."
    )
