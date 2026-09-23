"""
Integration tests for Backup & DR Phase 1 (2026-09):
GET /admin/backup/status and POST /admin/backup/run.

Neither route can be fully exercised end-to-end here without real R2
credentials (this test environment deliberately has none — see
.env.example) — these tests pin the two things that matter regardless
of whether backup is actually configured: the platform-admin gate is
real, and a missing-config attempt fails with a clear, named reason
rather than a crash.
"""

import base64
import json


def _extract_user_id_from_token(headers):
    token = headers["Authorization"].split(" ")[1]
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    return payload["sub"]


def test_backup_status_requires_auth(client):
    resp = client.get("/admin/backup/status")
    assert resp.status_code in (401, 403)


def test_backup_run_requires_auth(client):
    resp = client.post("/admin/backup/run")
    assert resp.status_code in (401, 403)


def test_regular_tenant_admin_cannot_read_backup_status(client, auth_headers):
    """
    A backup is a full dump of EVERY tenant's data — a stricter gate
    than every ingestion route (admin/analyst, tenant-scoped). A
    freshly signed-up user IS their own tenant's admin by design (see
    CLAUDE.md), so this is the real boundary to prove, not a
    hypothetical one.
    """
    resp = client.get("/admin/backup/status", headers=auth_headers)
    assert resp.status_code == 403


def test_regular_tenant_admin_cannot_trigger_backup(client, auth_headers):
    resp = client.post("/admin/backup/run", headers=auth_headers)
    assert resp.status_code == 403


def test_platform_admin_can_read_backup_status_shape(client, auth_headers, db_cursor):
    """
    Phase 4 (2026-09): status nests two separate health checks —
    "did a backup recently upload" and "was a backup recently proven
    restorable" are different claims (see app/backup.py's
    restore_drill_health docstring) and must not be flattened into
    one.
    """
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )
    resp = client.get("/admin/backup/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "backup" in body
    assert "restore_drill" in body
    for section in (body["backup"], body["restore_drill"]):
        for key in ("configured", "last_success_at", "last_run_status", "health"):
            assert key in section
        assert section["health"] in ("healthy", "stale", "never_run")


def test_verify_restore_requires_platform_admin(client, auth_headers):
    resp = client.post("/admin/backup/verify-restore", headers=auth_headers)
    assert resp.status_code == 403


def test_verify_restore_requires_auth(client):
    resp = client.post("/admin/backup/verify-restore")
    assert resp.status_code in (401, 403)


def test_backup_run_fails_clearly_when_not_configured(client, auth_headers, db_cursor):
    """
    When R2 credentials are genuinely unset (see .env.example), a
    platform admin triggering a backup must get a clear, actionable
    400 naming exactly what's missing, not a raw 500 traceback.
    Skipped once real credentials exist (2026-09-22: this project's
    own dev environment now has a real R2 bucket configured, verified
    end-to-end by hand — see CLAUDE.md's Backup & DR entry) — there is
    no safe way to unset a live process's already-loaded credentials
    mid-suite without disrupting the real scheduled backup job, and a
    test asserting the unconfigured path against an environment that
    is, correctly, now configured would itself be the stale one.
    """
    db_cursor.execute(
        "update users set is_platform_admin = true where id = %s",
        (_extract_user_id_from_token(auth_headers),),
    )
    status = client.get("/admin/backup/status", headers=auth_headers).json()
    if status["backup"]["configured"]:
        import pytest
        pytest.skip("R2 credentials are genuinely configured in this environment — the unconfigured path can't be safely exercised here.")

    resp = client.post("/admin/backup/run", headers=auth_headers)
    assert resp.status_code == 400
    assert "not configured" in resp.json()["detail"]
