"""
Data retention policy (2026-09) — pure logic, DB access via a passed
session, same separation as app/backup.py.

The real gap this closes: audit_log and the one-time auth token
tables (password_reset_tokens, email_verification_tokens) have grown
since day one with nothing that ever deletes an old row. "How long do
we keep X" had no defined, enforced answer anywhere in this codebase.

Two different windows, for two different reasons:
  - AUDIT_LOG_RETENTION_DAYS (default 2 years): audit_log is a
    security/compliance record, not operational data — the window is
    long on purpose. Configurable via env for a deployment under a
    specific regulatory regime this default doesn't fit.
  - TOKEN_RETENTION_DAYS (default 30 days): password_reset_tokens and
    email_verification_tokens only matter for a few minutes/hours
    (see PASSWORD_RESET_TOKEN_MINUTES / EMAIL_VERIFICATION_TOKEN_MINUTES
    in main.py) — a row that's long past expires_at, OR already used,
    has zero remaining function. Kept for a short buffer past that
    (not deleted the instant they expire) purely so a real incident
    investigation in the days right after has something to look at;
    30 days is generous for that without letting the tables grow
    forever. Only EXPIRED-OR-USED rows are ever touched — a still-live,
    unused token is never a retention-purge target regardless of age.

Reuses backup_jobs (see migration 049's own header for why) for run
tracking, the same _job_health-compatible shape backup/restore_drill
already use.
"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

AUDIT_LOG_RETENTION_DAYS = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "730"))
TOKEN_RETENTION_DAYS = int(os.environ.get("TOKEN_RETENTION_DAYS", "30"))

# audit_log carries a real RLS policy (tenant_isolation_audit_log) —
# unlike backup_jobs/password_reset_tokens/email_verification_tokens,
# which don't. A purge run through the app's normal RLS-scoped
# connection (doi_app, app.current_tenant set to whichever platform
# admin happened to trigger it) would silently only ever delete THAT
# ONE tenant's old audit_log rows — a real bug found live while
# testing this: 0 rows deleted from a seeded 750-day-old row with the
# 730-day default window, root-caused to RLS filtering it out, not
# the age math being wrong. Needs the SAME privileged, non-RLS-scoped
# connection app/backup.py's own pg_dump already uses (and for the
# identical reason — see that module's own docstring), so a dedicated
# engine on ALEMBIC_DATABASE_URL is used for the audit_log delete
# specifically, not the app's normal doi_app connection.
_PRIVILEGED_DATABASE_URL = os.environ.get("ALEMBIC_DATABASE_URL", "")
_privileged_engine = create_async_engine(_PRIVILEGED_DATABASE_URL, echo=False) if _PRIVILEGED_DATABASE_URL else None
_PrivilegedSession = async_sessionmaker(_privileged_engine, expire_on_commit=False) if _privileged_engine else None


async def run_retention_purge(session: AsyncSession, triggered_by: str = "scheduler") -> dict:
    job_id = (await session.execute(
        text("insert into backup_jobs (status, job_type) values ('running', 'retention_purge') returning id")
    )).scalar_one()
    await session.commit()

    try:
        now = datetime.now(timezone.utc)
        audit_cutoff = now - timedelta(days=AUDIT_LOG_RETENTION_DAYS)
        token_cutoff = now - timedelta(days=TOKEN_RETENTION_DAYS)

        if _PrivilegedSession is None:
            raise RuntimeError("ALEMBIC_DATABASE_URL is not set — required to purge audit_log across all tenants (it carries RLS)")
        async with _PrivilegedSession() as privileged:
            audit_deleted = (await privileged.execute(
                text("delete from audit_log where created_at < :cutoff"),
                {"cutoff": audit_cutoff},
            )).rowcount
            await privileged.commit()

        reset_tokens_deleted = (await session.execute(
            text("""
                delete from password_reset_tokens
                where created_at < :cutoff
                  and (used_at is not null or expires_at < now())
            """),
            {"cutoff": token_cutoff},
        )).rowcount

        verify_tokens_deleted = (await session.execute(
            text("""
                delete from email_verification_tokens
                where created_at < :cutoff
                  and (used_at is not null or expires_at < now())
            """),
            {"cutoff": token_cutoff},
        )).rowcount

        details = {
            "audit_log_deleted": audit_deleted,
            "password_reset_tokens_deleted": reset_tokens_deleted,
            "email_verification_tokens_deleted": verify_tokens_deleted,
            "audit_log_retention_days": AUDIT_LOG_RETENTION_DAYS,
            "token_retention_days": TOKEN_RETENTION_DAYS,
        }

        await session.execute(
            text("""
                update backup_jobs
                set status = 'succeeded', finished_at = now(), details = CAST(:details AS jsonb)
                where id = :id
            """),
            {"id": job_id, "details": _to_json(details)},
        )
        await session.commit()
        return {"job_id": str(job_id), "status": "succeeded", **details}
    except Exception as e:
        # A failed statement earlier in this same transaction leaves
        # it poisoned — Postgres refuses every further command with
        # "current transaction is aborted" until a rollback, which
        # would otherwise mask the real error with a second, more
        # confusing one right here. Found live: a bug in the delete
        # itself surfaced as this masking error, not the real one.
        await session.rollback()
        await session.execute(
            text("update backup_jobs set status = 'failed', finished_at = now(), error = :err where id = :id"),
            {"id": job_id, "err": str(e)[:1000]},
        )
        await session.commit()
        raise


def _to_json(d: dict) -> str:
    import json
    return json.dumps(d)


async def retention_health(session: AsyncSession) -> dict:
    """Same job_health shape as backup_health/restore_drill_health — imported lazily to avoid a circular import (app.backup already imports nothing from here)."""
    from app.backup import job_health

    # A purge is expected roughly daily (see the scheduler job in
    # main.py) — 72h tolerates a couple of missed runs before
    # flagging, more slack than backup's own 48h since a slightly
    # stale purge has no correctness impact, only a slower cleanup.
    health = await job_health(session, "retention_purge", threshold_hours=72)
    return {
        "audit_log_retention_days": AUDIT_LOG_RETENTION_DAYS,
        "token_retention_days": TOKEN_RETENTION_DAYS,
        **health,
    }
