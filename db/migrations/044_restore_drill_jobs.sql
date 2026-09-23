-- ============================================================
-- Backup & DR Phase 4 (2026-09) — automated restore-drill tracking.
--
-- Reuses backup_jobs (migration 043) rather than a new near-identical
-- table: a `job_type` column distinguishes a real backup attempt
-- from a restore-drill attempt (download the latest backup, decrypt,
-- restore into a scratch database, verify row counts, tear down) —
-- same status/started_at/finished_at/error shape either way, so one
-- health query can answer "when did a backup last succeed" and
-- "when did a restore last actually get PROVEN to work" with the
-- same simple filter, not two divergent schemas.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/044_restore_drill_jobs.sql
-- ============================================================

alter table backup_jobs
    add column job_type text not null default 'backup'
        check (job_type in ('backup', 'restore_drill'));

comment on column backup_jobs.job_type is
    'backup = a real pg_dump taken and uploaded; restore_drill = a periodic proof that a stored backup can actually be restored (Phase 4, 2026-09).';
