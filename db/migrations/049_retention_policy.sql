-- ============================================================
-- Data retention policy (2026-09) — a real, standing gap: audit_log
-- and password_reset_tokens/email_verification_tokens have grown
-- since day one with no defined lifetime and nothing that ever
-- deletes an old row. "How long do we keep X" had no answer anywhere
-- in this codebase before now.
--
-- Reuses backup_jobs (migrations 043/044) for tracking a retention
-- purge run rather than a new near-identical table, the same
-- reasoning 044's own header gives for reusing it for restore drills:
-- one job_type-discriminated table means /admin/*/status's shared
-- _job_health() helper works for this too, with zero code changes.
--
-- `details` is a new, GENERIC jsonb column (not retention-specific)
-- for structured per-run results a bare `error` text column can't
-- hold on a SUCCESS (e.g. retention's own rows-deleted breakdown per
-- table) — available to any future job_type, not just this one.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/049_retention_policy.sql
-- ============================================================

alter table backup_jobs
    drop constraint backup_jobs_job_type_check,
    add constraint backup_jobs_job_type_check
        check (job_type in ('backup', 'restore_drill', 'retention_purge'));

alter table backup_jobs add column details jsonb;

comment on column backup_jobs.details is
    'Structured per-run result data, e.g. retention_purge''s rows-deleted-per-table breakdown. Generic — not tied to one job_type.';
