"""
GET /opportunities' has_action_path field (2026-09) — a real user
question ("what's the use of a lead with no way to act on it?") led
to computing, server-side, whether a matched tender actually has an
apply link OR a procurement contact published by its source. False
only when a programme has neither — never a platform bug, a real gap
in what the source itself published for that specific notice.
"""

import uuid


def _seed_programme(db_cursor, *, ui_link=None, contact_name=None, contact_email=None):
    db_cursor.execute("select id from sources limit 1")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-fixture-action-path-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref, ui_link, contact_name, contact_email)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Action Path Programme", "United States", "rfp_issued", source_id,
         "336411", fixture_ref, ui_link, contact_name, contact_email),
    )
    return str(db_cursor.fetchone()["id"])


def _seed_opportunity(db_cursor, tenant_id, product_id, programme_id):
    db_cursor.execute(
        "insert into opportunities (tenant_id, product_id, programme_id, stage) values (%s, %s, %s, 'lead') returning id",
        (tenant_id, product_id, programme_id),
    )
    return str(db_cursor.fetchone()["id"])


def test_a_programme_with_a_ui_link_has_an_action_path(client, auth_headers, db_cursor):
    product_id = client.post("/products", headers=auth_headers, json={"name": "Action Path Product A"}).json()["id"]
    db_cursor.execute("select tenant_id from products where id = %s", (product_id,))
    tenant_id = db_cursor.fetchone()["tenant_id"]

    programme_id = _seed_programme(db_cursor, ui_link="https://sam.gov/workspace/contract/opp/fake/view")
    opp_id = _seed_opportunity(db_cursor, tenant_id, product_id, programme_id)

    rows = client.get("/opportunities", headers=auth_headers).json()
    row = next(r for r in rows if r["id"] == opp_id)
    assert row["has_action_path"] is True


def test_a_programme_with_only_a_contact_has_an_action_path(client, auth_headers, db_cursor):
    product_id = client.post("/products", headers=auth_headers, json={"name": "Action Path Product B"}).json()["id"]
    db_cursor.execute("select tenant_id from products where id = %s", (product_id,))
    tenant_id = db_cursor.fetchone()["tenant_id"]

    programme_id = _seed_programme(db_cursor, contact_email="buyer@example.gov")
    opp_id = _seed_opportunity(db_cursor, tenant_id, product_id, programme_id)

    rows = client.get("/opportunities", headers=auth_headers).json()
    row = next(r for r in rows if r["id"] == opp_id)
    assert row["has_action_path"] is True


def test_a_programme_with_neither_has_no_action_path(client, auth_headers, db_cursor):
    product_id = client.post("/products", headers=auth_headers, json={"name": "Action Path Product C"}).json()["id"]
    db_cursor.execute("select tenant_id from products where id = %s", (product_id,))
    tenant_id = db_cursor.fetchone()["tenant_id"]

    programme_id = _seed_programme(db_cursor)  # no ui_link, no contact
    opp_id = _seed_opportunity(db_cursor, tenant_id, product_id, programme_id)

    rows = client.get("/opportunities", headers=auth_headers).json()
    row = next(r for r in rows if r["id"] == opp_id)
    assert row["has_action_path"] is False
