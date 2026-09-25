"""
Integration tests for the data retention policy (2026-09, migration
049): POST /admin/retention/run purges audit_log rows older than
AUDIT_LOG_RETENTION_DAYS and expired-or-used auth tokens older than
TOKEN_RETENTION_DAYS, while leaving recent/still-live rows untouched.

audit_log carries a real RLS policy (unlike backup_jobs/
password_reset_tokens/email_verification_tokens) — a real bug found
while building this: a purge run through the app's normal RLS-scoped
connection silently deleted 0 rows, since app.current_tenant only
ever matches the platform admin's own tenant, never every tenant's
old rows. app/retention.py now uses a dedicated privileged connection
(ALEMBIC_DATABASE_URL, same role app/backup.py's own pg_dump already
trusts) specifically for the audit_log delete. This suite seeds an
audit_log row for a SEPARATE tenant (not the admin's own) specifically
to prove that fix — a same-tenant-only test would not have caught the
original bug.
"""

import uuid


def _extract_user_id_from_token(headers):
    import base64
    import json

    token = headers["Authorization"].split(" ")[1]
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    return payload["sub"]


def _grant_platform_admin(db_cursor, auth_headers):
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )


def _second_tenant_ids(client, db_cursor):
    email = f"retention-other-{uuid.uuid4().hex[:10]}@example.com"
    resp = client.post(
        "/auth/signup",
        json={"company_name": "Retention Other Tenant", "full_name": "Other Admin", "email": email, "password": "another-real-password-1"},
    )
    assert resp.status_code == 201
    db_cursor.execute("select id, tenant_id from users where email = %s", (email,))
    row = db_cursor.fetchone()
    return row["id"], row["tenant_id"]


def test_retention_purge_deletes_old_rows_across_every_tenant_but_keeps_recent_ones(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)

    # A different tenant than the admin's own — proves the purge isn't
    # silently scoped to just the caller's tenant (the real bug found
    # live; see module docstring).
    other_user_id, other_tenant_id = _second_tenant_ids(client, db_cursor)

    db_cursor.execute(
        """
        insert into audit_log (tenant_id, user_id, action, entity_type, created_at) values
        (%s, %s, 'test.retention_old', 'user', now() - interval '800 days'),
        (%s, %s, 'test.retention_recent', 'user', now() - interval '5 days')
        """,
        (other_tenant_id, other_user_id, other_tenant_id, other_user_id),
    )
    old_token_hash = f"retention-test-old-expired-{uuid.uuid4().hex}"
    recent_token_hash = f"retention-test-recent-expired-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into password_reset_tokens (user_id, token_hash, expires_at, used_at, created_at) values
        (%s, %s, now() - interval '35 days', null, now() - interval '35 days'),
        (%s, %s, now() - interval '1 day', null, now() - interval '2 days')
        """,
        (other_user_id, old_token_hash, other_user_id, recent_token_hash),
    )

    resp = client.post("/admin/retention/run", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["audit_log_deleted"] >= 1
    assert body["password_reset_tokens_deleted"] >= 1

    db_cursor.execute("select action from audit_log where tenant_id = %s", (other_tenant_id,))
    remaining_actions = {row["action"] for row in db_cursor.fetchall()}
    assert "test.retention_old" not in remaining_actions
    assert "test.retention_recent" in remaining_actions

    db_cursor.execute("select token_hash from password_reset_tokens where user_id = %s", (other_user_id,))
    remaining_hashes = {row["token_hash"] for row in db_cursor.fetchall()}
    assert old_token_hash not in remaining_hashes
    assert recent_token_hash in remaining_hashes


def test_retention_run_requires_platform_admin(client, auth_headers):
    resp = client.post("/admin/retention/run", headers=auth_headers)
    assert resp.status_code == 403


def test_retention_status_reflects_a_successful_run(client, auth_headers, db_cursor):
    _grant_platform_admin(db_cursor, auth_headers)

    run_resp = client.post("/admin/retention/run", headers=auth_headers)
    assert run_resp.status_code == 200

    status_resp = client.get("/admin/backup/status", headers=auth_headers)
    assert status_resp.status_code == 200
    retention = status_resp.json()["retention_purge"]
    assert retention["health"] == "healthy"
    assert retention["last_run_status"] == "succeeded"
