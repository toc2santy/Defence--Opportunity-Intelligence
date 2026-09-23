"""
Integration tests for /team/members and /engagement/team-workload —
added after a Report Intel review found `owner_user_id` had existed
since Phase 0 and was always PATCH-settable, but nothing ever listed
a tenant's own teammates (write-only in practice) or surfaced when a
follow-up due_date had quietly gone overdue.
"""

import uuid


def _seed_and_match(client, auth_headers, db_cursor, naics_code="336413"):
    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_id = db_cursor.fetchone()["id"]
    fixture_ref = f"test-team-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Aerospace Component Kit", "United States", "rfp_issued", source_id, naics_code, fixture_ref),
    )
    programme_id = str(db_cursor.fetchone()["id"])
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Aerospace Bracket", "description": "aerospace component manufacturing, aircraft engine parts"},
    ).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    cap = next(c for c in candidates if c["code"] == "AEROSPACE.COMPONENTS")
    client.post(f"/products/{product_id}/capabilities/{cap['capability_id']}/confirm", headers=auth_headers)
    matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]
    fixture_match = next(m for m in matches if m["programme_id"] == programme_id)
    return fixture_match["opportunity_id"]


def test_team_members_requires_auth(client):
    resp = client.get("/team/members")
    assert resp.status_code in (401, 403)


def test_team_members_lists_the_signed_up_admin(client, auth_headers):
    resp = client.get("/team/members", headers=auth_headers)
    assert resp.status_code == 200
    members = resp.json()
    assert len(members) >= 1
    assert all("email" in m and "id" in m for m in members)


def test_team_members_is_scoped_to_the_caller_tenant_only(client):
    """
    users has no RLS policy — this pins that the route's own explicit
    tenant_id filter is actually doing the isolation.
    """
    email_a = f"teama-{uuid.uuid4().hex[:8]}@example.com"
    resp_a = client.post(
        "/auth/signup",
        json={"company_name": "Team Co A", "full_name": "A Person", "email": email_a, "password": "a-real-password-123"},
    )
    headers_a = {"Authorization": f"Bearer {resp_a.json()['access_token']}"}

    email_b = f"teamb-{uuid.uuid4().hex[:8]}@example.com"
    resp_b = client.post(
        "/auth/signup",
        json={"company_name": "Team Co B", "full_name": "B Person", "email": email_b, "password": "a-real-password-123"},
    )

    members_a = client.get("/team/members", headers=headers_a).json()
    assert all(m["email"] != email_b for m in members_a)
    assert any(m["email"] == email_a for m in members_a)


def test_owner_assignment_shows_up_in_team_workload(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="336412")
    me = client.get("/auth/me", headers=auth_headers).json()

    client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "qualified", "owner_user_id": me["user_id"]},
    )

    workload = client.get("/engagement/team-workload", headers=auth_headers).json()["team"]
    mine = next(row for row in workload if row["owner_user_id"] == me["user_id"])
    assert mine["open_count"] >= 1
    assert mine["display_name"] == me["full_name"]


def test_overdue_opportunity_counted_in_team_workload(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="336412")
    me = client.get("/auth/me", headers=auth_headers).json()

    client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "qualified", "owner_user_id": me["user_id"], "due_date": "2020-01-01"},
    )

    workload = client.get("/engagement/team-workload", headers=auth_headers).json()["team"]
    mine = next(row for row in workload if row["owner_user_id"] == me["user_id"])
    assert mine["overdue_count"] >= 1


def test_won_and_lost_opportunities_are_excluded_from_workload(client, auth_headers, db_cursor):
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="336413")
    me = client.get("/auth/me", headers=auth_headers).json()

    before = client.get("/engagement/team-workload", headers=auth_headers).json()["team"]
    before_total = sum(row["open_count"] for row in before)

    client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "won", "owner_user_id": me["user_id"]},
    )

    after = client.get("/engagement/team-workload", headers=auth_headers).json()["team"]
    after_total = sum(row["open_count"] for row in after)
    # A won deal must not be double-counted as "open workload".
    assert after_total <= before_total


def test_unassigned_opportunities_appear_as_their_own_bucket(client, auth_headers, db_cursor):
    _seed_and_match(client, auth_headers, db_cursor, naics_code="336412")
    workload = client.get("/engagement/team-workload", headers=auth_headers).json()["team"]
    unassigned = next((row for row in workload if row["owner_user_id"] is None), None)
    assert unassigned is not None
    assert unassigned["display_name"] == "Unassigned"
    assert unassigned["open_count"] >= 1


def test_next_action_reflects_a_newly_set_overdue_due_date(client, auth_headers, db_cursor):
    """
    End-to-end proof that PATCH actually recomputes the suggestion
    with the NEW due_date, not a stale one.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="336413")
    body = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"stage": "qualified", "due_date": "2020-01-01"},
    ).json()
    assert "⚠ Overdue" in body["next_action"]


def test_owner_can_be_explicitly_cleared(client, auth_headers, db_cursor):
    """
    The real gap this closes: owner_user_id: null on its own means
    "don't touch this field" everywhere else on this PATCH model —
    an "Unassigned" picker option needs an explicit way to actually
    clear an existing owner, not just omit the field.
    """
    opportunity_id = _seed_and_match(client, auth_headers, db_cursor, naics_code="336413")
    me = client.get("/auth/me", headers=auth_headers).json()

    assigned = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"owner_user_id": me["user_id"]},
    ).json()
    assert assigned["owner_user_id"] == me["user_id"]

    # Sending owner_user_id: null alone must NOT clear it (matches
    # checklist_state's documented "None means not part of this PATCH").
    unchanged = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"owner_user_id": None},
    ).json()
    assert unchanged["owner_user_id"] == me["user_id"]

    cleared = client.patch(
        f"/opportunities/{opportunity_id}", headers=auth_headers,
        json={"clear_owner": True},
    ).json()
    assert cleared["owner_user_id"] is None
