"""
PATCH /opportunities/{id}'s owner_user_id, and GET /engagement/team-
workload's read of it, both need their own tenant check because
`users` carries no RLS (see /team/members' own docstring in main.py)
— opportunities.owner_user_id only has a global FK (any user, any
tenant), so nothing at the database level stops one tenant's PATCH
from pointing an opportunity's owner at a completely different
tenant's user id. Found in a manual IDOR audit (2026-09): without the
fix, that would let a malicious tenant admin disclose an arbitrary
user's full_name/email (cross-tenant PII) via /engagement/team-
workload's join, since that query had no tenant filter on the users
side either.
"""

import uuid


def _create_product_and_opportunity(client, headers, db_cursor, name="Owner Isolation Test Product"):
    """Products go through the real API (RLS-scoped); opportunities have
    no create route (they only ever come from the matching engine), so
    this seeds one directly — test SETUP only, same discipline
    conftest.py documents for DB_DSN."""
    resp = client.post("/products", headers=headers, json={"name": name})
    assert resp.status_code == 201
    product_id = resp.json()["id"]

    db_cursor.execute("select tenant_id from products where id = %s", (product_id,))
    tenant_id = db_cursor.fetchone()["tenant_id"]

    db_cursor.execute(
        "insert into opportunities (tenant_id, product_id, stage) values (%s, %s, 'lead') returning id",
        (tenant_id, product_id),
    )
    return db_cursor.fetchone()["id"], tenant_id


def _second_tenant(client):
    email = f"owner-iso-b-{uuid.uuid4().hex[:10]}@example.com"
    resp = client.post(
        "/auth/signup",
        json={"company_name": "Owner Isolation Tenant B", "full_name": "Admin B", "email": email, "password": "another-real-password-1"},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    return {"token": token, "email": email}


def test_owner_user_id_cannot_be_set_to_a_different_tenants_user(client, auth_headers, new_tenant, db_cursor):
    opp_id, _tenant_id = _create_product_and_opportunity(client, auth_headers, db_cursor)

    tenant_b = _second_tenant(client)
    db_cursor.execute("select id from users where email = %s", (tenant_b["email"],))
    user_b_id = db_cursor.fetchone()["id"]

    resp = client.patch(
        f"/opportunities/{opp_id}",
        headers=auth_headers,
        json={"owner_user_id": str(user_b_id)},
    )
    assert resp.status_code == 422


def test_owner_user_id_can_be_set_to_the_same_tenants_user(client, auth_headers, new_tenant, db_cursor):
    opp_id, _tenant_id = _create_product_and_opportunity(client, auth_headers, db_cursor)

    db_cursor.execute("select id from users where email = %s", (new_tenant["email"],))
    own_user_id = db_cursor.fetchone()["id"]

    resp = client.patch(
        f"/opportunities/{opp_id}",
        headers=auth_headers,
        json={"owner_user_id": str(own_user_id)},
    )
    assert resp.status_code == 200
    assert resp.json()["owner_user_id"] == str(own_user_id)


def test_team_workload_never_discloses_a_cross_tenant_owners_identity(client, auth_headers, new_tenant, db_cursor):
    """
    Defense in depth: even if a row somehow ends up with a
    cross-tenant owner_user_id (a pre-existing row from before this
    fix, or any future bug that bypasses the PATCH-time check), the
    read side must still never disclose that other user's name/email.
    Seeded directly via db_cursor specifically to bypass the PATCH
    validation and prove the read-side guard holds independently.
    """
    opp_id, _tenant_id = _create_product_and_opportunity(client, auth_headers, db_cursor)

    tenant_b = _second_tenant(client)
    db_cursor.execute("select id from users where email = %s", (tenant_b["email"],))
    user_b_id = db_cursor.fetchone()["id"]

    db_cursor.execute(
        "update opportunities set owner_user_id = %s where id = %s",
        (user_b_id, opp_id),
    )

    resp = client.get("/engagement/team-workload", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()["team"]
    matching = [r for r in rows if r["owner_user_id"] == str(user_b_id)]
    assert len(matching) == 1
    assert matching[0]["full_name"] is None
    assert matching[0]["email"] is None
    assert matching[0]["display_name"] == "Unassigned"
