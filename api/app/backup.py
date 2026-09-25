"""
Backup & DR — Phase 1 (2026-09).

A daily, automated, ENCRYPTED, OFF-HOST database backup — the real
gap this closes: before this, the only copy of every tenant's data
was a single Docker named volume on one host (see docker-compose.yml's
`doi_pgdata`), which is durable against a container restart but not
against losing that host/disk at all. A backup that lives on the same
disk as what it's backing up is not disaster recovery.

Deliberately NOT a pure-Python re-implementation of pg_dump's binary
format — shells out to the real `pg_dump` (installed via
postgresql-client in api/Dockerfile, version-matched to the
postgres:16 image in docker-compose.yml) via asyncio's own subprocess
API, so this never blocks the event loop the way `subprocess.run`
would.

Runs as the SAME privileged role Alembic already uses
(ALEMBIC_DATABASE_URL, `postgres`) rather than the app's own `doi_app`
— a backup taken through a Row-Level-Security-scoped connection with
no tenant context set would be an incomplete or empty backup
depending on how RLS's default-deny behaves for an unset session
variable, and a "backup" that silently misses tenant data because of
who took it would be worse than no backup, since it would look
successful. Bypassing RLS here is correct and intentional, the same
justification Alembic's own role already relies on.

Encryption is symmetric (Fernet, from the `cryptography` package,
pure Python — no GPG/age binary dependency to add on top of
postgresql-client) rather than an asymmetric keypair: Phase 1 has one
operator, not a team needing separate restore keys, so a single
shared secret (BACKUP_ENCRYPTION_KEY) is the right amount of
complexity for this stage, not a shortcut taken under time pressure.
Revisit if/when Phase 3 (a standby replica, a real ops team) makes
per-person key management worth the overhead.

Retention (DAILY_RETENTION / MONTHLY_RETENTION below) is enforced
here, not left to the storage provider's own lifecycle rules, so the
policy is visible and versioned in code rather than configured once
in a cloud console and forgotten.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import boto3
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.email_sender import send_email

# The DB connection this module dumps FROM — deliberately the same
# privileged URL Alembic uses, not DATABASE_URL (the app's own
# RLS-scoped doi_app connection). See this module's own docstring for
# why an RLS-scoped dump would be a real correctness risk, not a
# style preference.
BACKUP_SOURCE_DATABASE_URL = os.environ.get("ALEMBIC_DATABASE_URL", "")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BACKUP_BUCKET = os.environ.get("R2_BACKUP_BUCKET", "defence-oi-backups")
BACKUP_ENCRYPTION_KEY = os.environ.get("BACKUP_ENCRYPTION_KEY")

DAILY_RETENTION = 30
MONTHLY_RETENTION = 12

# Phase 4 (2026-09) — a monthly base or daily dump nobody ever
# actually restores is unverified, not safe; RESTORE_DRILL_TABLES are
# a fixed internal constant (never external input), so interpolating
# them directly into SQL below is safe — nothing here is
# user-supplied. RESTORE_DRILL_DB is dropped and recreated every
# drill, never left behind on success, and dropped defensively before
# create too in case a previous run crashed mid-drill.
RESTORE_DRILL_DB = "doi_restore_drill"
RESTORE_DRILL_TABLES = ["programmes", "opportunities", "tenants", "users"]

# Where a failed backup/restore-drill gets emailed (Phase 4) — reuses
# app/email_sender.py's own send_email(), which already has a
# dev-mode fallback (logs instead of sending when SMTP_HOST is unset)
# — same "quietly degrade, never crash on a missing prerequisite"
# rule as everywhere else in this module. Deliberately its own env
# var, not reusing any tenant's email — this is platform-operator
# alerting, not tenant-facing.
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")


class BackupConfigError(Exception):
    """Raised when a required backup credential/setting is missing — mirrors IngestionConfigError's own shape (app/ingestion_common.py), same reasoning: a missing prerequisite should be a clear, named error, not an opaque crash three calls deep."""


def _require_config() -> None:
    missing = [
        name for name, val in (
            ("R2_ACCOUNT_ID", R2_ACCOUNT_ID),
            ("R2_ACCESS_KEY_ID", R2_ACCESS_KEY_ID),
            ("R2_SECRET_ACCESS_KEY", R2_SECRET_ACCESS_KEY),
            ("BACKUP_ENCRYPTION_KEY", BACKUP_ENCRYPTION_KEY),
        ) if not val
    ]
    if missing:
        raise BackupConfigError(
            f"Backup is not configured yet — missing: {', '.join(missing)}. "
            "Set these in .env (see .env.example) before backups can run."
        )


def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",  # R2 has no real regions — "auto" is Cloudflare's own documented value for the boto3 S3 client
    )


def _parse_db_url(url: str) -> dict:
    """
    postgresql+asyncpg://user:pass@host:port/dbname -> the plain
    pieces pg_dump's own CLI flags want. Written by hand rather than
    pulled in via sqlalchemy.engine.url (already a dependency) to
    keep this module's only real external dependencies limited to
    what backup itself needs — boto3 and cryptography — not an excuse
    to add a third.
    """
    # Strip the +asyncpg dialect suffix pg_dump doesn't understand.
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    without_scheme = url.split("://", 1)[1]
    creds, hostpart = without_scheme.split("@", 1)
    user, password = creds.split(":", 1)
    hostport, dbname = hostpart.split("/", 1)
    host, port = hostport.split(":", 1)
    return {"user": user, "password": password, "host": host, "port": port, "dbname": dbname}


async def _run_cmd(*args: str, env: dict) -> tuple[int, str]:
    """Shared subprocess runner (Phase 4) — same non-blocking asyncio.create_subprocess_exec this module already used for pg_dump, now reused for createdb/dropdb/pg_restore too rather than three near-duplicate call sites."""
    proc = await asyncio.create_subprocess_exec(
        *args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


def _alert(subject: str, body: str) -> None:
    if not ALERT_EMAIL:
        print(f"[backup alert] ALERT_EMAIL not configured — would have sent:\nSubject: {subject}\n{body}")
        return
    send_email(ALERT_EMAIL, subject, body)


async def _run_pg_dump(dest_path: str) -> None:
    conn = _parse_db_url(BACKUP_SOURCE_DATABASE_URL)
    env = {**os.environ, "PGPASSWORD": conn["password"]}
    # -Fc = pg_dump's own custom compressed format — smaller than
    # plain SQL and supports selective/parallel restore later, at no
    # cost over plain -Fp for a database this size.
    proc = await asyncio.create_subprocess_exec(
        "pg_dump", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
        "-Fc", "-f", dest_path, conn["dbname"],
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {stderr.decode(errors='replace')[:500]}")


def _encrypt_file(src_path: str, dest_path: str) -> int:
    fernet = Fernet(BACKUP_ENCRYPTION_KEY.encode())
    with open(src_path, "rb") as f:
        plaintext = f.read()
    ciphertext = fernet.encrypt(plaintext)
    with open(dest_path, "wb") as f:
        f.write(ciphertext)
    return len(ciphertext)


def _prune_old_backups(client) -> None:
    """
    Keeps DAILY_RETENTION most recent daily/ keys and
    MONTHLY_RETENTION most recent monthly/ keys, deletes the rest.
    Enforced here rather than via the bucket's own lifecycle rules
    (see module docstring) — every backup is written to BOTH a
    daily/ and, once a month, also a monthly/ key, so the same dump
    can outlive its 30-day daily slot under the monthly policy
    without a second pg_dump run.
    """
    for prefix, keep in (("daily/", DAILY_RETENTION), ("monthly/", MONTHLY_RETENTION)):
        objects = client.list_objects_v2(Bucket=R2_BACKUP_BUCKET, Prefix=prefix).get("Contents", [])
        objects.sort(key=lambda o: o["Key"], reverse=True)  # keys are timestamp-prefixed, so lexical sort is chronological
        for obj in objects[keep:]:
            client.delete_object(Bucket=R2_BACKUP_BUCKET, Key=obj["Key"])


async def run_backup(session: AsyncSession, triggered_by: str = "scheduler") -> dict:
    _require_config()

    job_id = (await session.execute(
        text("insert into backup_jobs (status, job_type) values ('running', 'backup') returning id")
    )).scalar_one()
    await session.commit()

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    dump_path = f"/tmp/doi-backup-{timestamp}.dump"
    encrypted_path = f"{dump_path}.enc"

    try:
        await _run_pg_dump(dump_path)
        size_bytes = _encrypt_file(dump_path, encrypted_path)

        client = _r2_client()
        keys = [f"daily/{timestamp}.dump.enc"]
        # First backup of the calendar month also gets a monthly/ copy
        # — same file uploaded twice under different retention rules,
        # not a second dump.
        if now.day == 1 or triggered_by == "manual_monthly":
            keys.append(f"monthly/{timestamp}.dump.enc")
        for key in keys:
            with open(encrypted_path, "rb") as f:
                client.put_object(Bucket=R2_BACKUP_BUCKET, Key=key, Body=f)
        _prune_old_backups(client)

        await session.execute(
            text("""
                update backup_jobs
                set status = 'succeeded', finished_at = now(), storage_key = :key, size_bytes = :size
                where id = :id
            """),
            {"id": job_id, "key": keys[0], "size": size_bytes},
        )
        await session.commit()
        return {"job_id": str(job_id), "status": "succeeded", "storage_key": keys[0], "size_bytes": size_bytes}
    except Exception as e:
        await session.execute(
            text("update backup_jobs set status = 'failed', finished_at = now(), error = :err where id = :id"),
            {"id": job_id, "err": str(e)[:1000]},
        )
        await session.commit()
        _alert(
            "⚠ Defence OI: database backup FAILED",
            f"Backup job {job_id} failed at {datetime.now(timezone.utc).isoformat()}.\n\nError: {e}\n\n"
            f"Check GET /admin/backup/status for the current health state.",
        )
        raise
    finally:
        for p in (dump_path, encrypted_path):
            if os.path.exists(p):
                os.remove(p)


async def run_restore_drill(session: AsyncSession, triggered_by: str = "scheduler") -> dict:
    """
    Phase 4 (2026-09) — the automated version of the by-hand restore
    verification Phase 1 did once manually (see CLAUDE.md's own entry
    on that): downloads the MOST RECENT daily/ backup from R2,
    decrypts it, validates the archive with `pg_restore --list`, then
    does a REAL `pg_restore` into a scratch database
    (RESTORE_DRILL_DB) and compares row counts on a few key tables
    against the live database. A backup that uploads successfully but
    can't actually be restored is a false sense of safety — this is
    what keeps that from going unnoticed between manual checks.

    Runs on the SAME privileged role as run_backup (see module
    docstring) — createdb/dropdb need it regardless.
    """
    _require_config()

    job_id = (await session.execute(
        text("insert into backup_jobs (status, job_type) values ('running', 'restore_drill') returning id")
    )).scalar_one()
    await session.commit()

    conn = _parse_db_url(BACKUP_SOURCE_DATABASE_URL)
    env = {**os.environ, "PGPASSWORD": conn["password"]}
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    dump_path = f"/tmp/restore-drill-{timestamp}.dump"
    drill_conn = None

    try:
        client = _r2_client()
        objects = client.list_objects_v2(Bucket=R2_BACKUP_BUCKET, Prefix="daily/").get("Contents", [])
        if not objects:
            raise RuntimeError("No daily/ backup exists in R2 yet to verify.")
        objects.sort(key=lambda o: o["Key"], reverse=True)
        latest_key = objects[0]["Key"]

        obj = client.get_object(Bucket=R2_BACKUP_BUCKET, Key=latest_key)
        encrypted = obj["Body"].read()
        fernet = Fernet(BACKUP_ENCRYPTION_KEY.encode())
        decrypted = fernet.decrypt(encrypted)
        with open(dump_path, "wb") as f:
            f.write(decrypted)

        rc, out = await _run_cmd("pg_restore", "--list", dump_path, env=env)
        if rc != 0:
            raise RuntimeError(f"pg_restore --list failed on {latest_key}: {out[:500]}")

        await _run_cmd("dropdb", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
                        "--if-exists", RESTORE_DRILL_DB, env=env)
        rc, out = await _run_cmd("createdb", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
                                  RESTORE_DRILL_DB, env=env)
        if rc != 0:
            raise RuntimeError(f"createdb failed: {out[:500]}")

        # pg_restore can exit non-zero on harmless warnings alone (a
        # real, observed example: `unrecognized configuration
        # parameter "transaction_timeout"` — a client/server Postgres
        # tooling-version mismatch on one SET statement, not a data
        # problem — see CLAUDE.md's Phase 1 restore-test entry). A
        # real failure is judged by whether the tables below are
        # actually populated and match, not by this exit code alone.
        await _run_cmd("pg_restore", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
                        "-d", RESTORE_DRILL_DB, "--no-owner", "--no-privileges", dump_path, env=env)

        # A real bug, caught by the FIRST live run of this drill
        # (2026-09-22): counting the live side through the request's
        # own RLS-scoped `session` is wrong twice over — it silently
        # narrows `opportunities` to only the calling admin's own
        # tenant (RLS doing exactly what it's supposed to, just not
        # what a whole-database backup comparison needs), and this
        # session's tenant context is set via `SET LOCAL` inside a
        # transaction this function's own earlier `session.commit()`
        # calls (inserting/updating the 'running' backup_jobs row)
        # already ended — so by the time this line ran, current_setting
        # returned empty and Postgres refused to cast '' to uuid. A
        # full-database backup must be verified against the FULL live
        # database, so both counts now come from direct, RLS-bypassing
        # connections to the real `doi` database and the scratch
        # restore database — apples to apples, neither filtered.
        live_conn = await asyncpg.connect(
            host=conn["host"], port=int(conn["port"]), user=conn["user"],
            password=conn["password"], database=conn["dbname"],
        )
        drill_conn = await asyncpg.connect(
            host=conn["host"], port=int(conn["port"]), user=conn["user"],
            password=conn["password"], database=RESTORE_DRILL_DB,
        )
        try:
            mismatches = []
            for table in RESTORE_DRILL_TABLES:
                live_count = await live_conn.fetchval(f"select count(*) from {table}")
                drill_count = await drill_conn.fetchval(f"select count(*) from {table}")
                # A real finding from the FIRST live run of this drill
                # (2026-09-22): exact equality is the wrong check on an
                # active database. The backup being verified was taken
                # HOURS before this drill runs, and real writes happen
                # in between (confirmed live: 51 new tenants signed up
                # in the 2 hours between that backup and this check,
                # explaining a 45-row gap exactly) — that is normal
                # growth, not data loss, and exact equality would flag
                # it as "loss" on every single run of an active
                # database. The two signals that ARE real problems:
                # the restored count is HIGHER than live (a backup
                # cannot legitimately contain rows that don't exist
                # yet — corruption/duplication), or it's LOWER by more
                # than a generous tolerance (real loss, not just the
                # gap since backup time). 10% tolerates normal growth
                # between a daily backup and a weekly drill without
                # tolerating an actually-broken restore.
                if drill_count > live_count:
                    mismatches.append(f"{table}: restored ({drill_count}) EXCEEDS live ({live_count}) — impossible unless corrupted")
                elif drill_count < live_count * 0.9:
                    mismatches.append(f"{table}: restored ({drill_count}) is more than 10% below live ({live_count})")
            if mismatches:
                raise RuntimeError(f"Restore drill row-count mismatch (real data-loss signal): {'; '.join(mismatches)}")
        finally:
            await live_conn.close()

        await session.execute(
            text("""
                update backup_jobs
                set status = 'succeeded', finished_at = now(), storage_key = :key
                where id = :id
            """),
            {"id": job_id, "key": latest_key},
        )
        await session.commit()
        return {"job_id": str(job_id), "status": "succeeded", "verified_backup": latest_key,
                "tables_checked": RESTORE_DRILL_TABLES}
    except Exception as e:
        await session.execute(
            text("update backup_jobs set status = 'failed', finished_at = now(), error = :err where id = :id"),
            {"id": job_id, "err": str(e)[:1000]},
        )
        await session.commit()
        _alert(
            "⚠ Defence OI: restore drill FAILED — a backup may not actually be restorable",
            f"Restore-drill job {job_id} failed at {datetime.now(timezone.utc).isoformat()}.\n\nError: {e}\n\n"
            f"This means the most recent backup could NOT be proven restorable — treat as urgent.",
        )
        raise
    finally:
        if drill_conn is not None:
            await drill_conn.close()
        await _run_cmd("dropdb", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
                        "--if-exists", RESTORE_DRILL_DB, env=env)
        if os.path.exists(dump_path):
            os.remove(dump_path)


async def job_health(session: AsyncSession, job_type: str, threshold_hours: int) -> dict:
    """Shared by backup_health, restore_drill_health (Phase 4), and app.retention.retention_health (2026-09) — same query, filtered to one job_type, so all three health checks can never structurally drift apart. Was private (`_job_health`) until retention needed it too; renamed rather than duplicated."""
    row = (await session.execute(
        text("""
            select max(finished_at) filter (where status = 'succeeded') as last_success_at,
                   (array_agg(status order by started_at desc))[1] as last_run_status,
                   (array_agg(started_at order by started_at desc))[1] as last_run_at,
                   (array_agg(error order by started_at desc))[1] as last_run_error
            from backup_jobs
            where job_type = :job_type
        """),
        {"job_type": job_type},
    )).first()

    last_success_at = row.last_success_at if row else None
    now = datetime.now(timezone.utc)
    hours_since_success = (now - last_success_at).total_seconds() / 3600 if last_success_at else None
    if last_success_at is None:
        health = "never_run"
    elif hours_since_success > threshold_hours:
        health = "stale"
    else:
        health = "healthy"

    return {
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
        "hours_since_last_success": round(hours_since_success, 1) if hours_since_success is not None else None,
        "last_run_status": row.last_run_status if row else None,
        "last_run_at": row.last_run_at.isoformat() if row and row.last_run_at else None,
        "last_run_error": row.last_run_error if row else None,
        "health_threshold_hours": threshold_hours,
        "health": health,
    }


async def backup_health(session: AsyncSession) -> dict:
    """
    Same shape as /ingestion/sources/status's health fields (2026-09)
    — a backup that "ran" but hasn't succeeded recently is exactly
    the kind of silent gap that feature was built to stop hiding, and
    a database backup deserves at least the same visibility a tender
    source's ingestion run already gets.
    """
    # One daily backup expected every 24h — 48h tolerates one missed
    # run before flagging.
    health = await job_health(session, "backup", threshold_hours=48)
    return {
        "configured": all((R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, BACKUP_ENCRYPTION_KEY)),
        **health,
    }


async def restore_drill_health(session: AsyncSession) -> dict:
    """
    Phase 4 (2026-09) — the same health shape as backup_health, for
    the restore-drill check instead. A recent successful BACKUP does
    not mean a recent successful RESTORE was ever proven — these are
    deliberately two separate health checks, not one flattened into
    the other, because "we uploaded something" and "we proved we can
    get it back" are different claims.
    """
    # One drill expected every 7 days (WEEKLY_RESTORE_DRILL below) —
    # 240h (10 days) tolerates one missed run before flagging.
    health = await job_health(session, "restore_drill", threshold_hours=240)
    return {
        "configured": all((R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, BACKUP_ENCRYPTION_KEY)),
        **health,
    }


async def check_staleness_and_alert(session: AsyncSession) -> None:
    """
    Phase 4 (2026-09) — a DIFFERENT failure mode from a job that ran
    and failed (already alerted on in run_backup/run_restore_drill's
    own except blocks): a job that never even FIRED at all, e.g. the
    scheduler process itself died, or was never restarted after a
    host reboot. Run daily, independent of whether backup/restore-
    drill jobs themselves ran that day — the only way to catch total
    silence, not just a loud failure.
    """
    if not all((R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, BACKUP_ENCRYPTION_KEY)):
        return  # not configured at all yet — nothing to be stale about, and Phase 1/2's own "skipped: not configured" logging already covers this
    backup = await backup_health(session)
    drill = await restore_drill_health(session)
    if backup["health"] == "stale":
        _alert(
            "⚠ Defence OI: no successful backup in over 48 hours",
            f"Last successful backup: {backup['last_success_at'] or 'never'}. "
            f"The scheduled daily backup job may not be running at all — check the API process is up "
            f"and the scheduler started (see startup logs).",
        )
    if drill["health"] == "stale":
        _alert(
            "⚠ Defence OI: no successful restore drill in over 10 days",
            f"Last successful restore drill: {drill['last_success_at'] or 'never'}. "
            f"Backups may be uploading fine while quietly becoming unrestorable — verify manually via "
            f"POST /admin/backup/verify-restore.",
        )
