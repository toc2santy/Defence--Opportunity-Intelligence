-- ============================================================
-- Backup & DR Phase 1 (2026-09) — a real, observable record of
-- every backup attempt, same shape and same reason as
-- ingestion_jobs: a backup job's own "succeeded" status only means
-- "the process didn't raise" — a history table is what lets a
-- health check later ask "when did one last ACTUALLY complete",
-- the same question source-health monitoring already answers for
-- ingestion. No RLS — this is platform infrastructure, not tenant
-- data, and only a platform admin can ever read it (see the route
-- in main.py, not a table-level policy).
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/043_backup_jobs.sql
-- ============================================================

create table backup_jobs (
    id uuid primary key default gen_random_uuid(),
    status text not null check (status in ('running', 'succeeded', 'failed')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    storage_key text,          -- the R2 object key this backup was written to, once uploaded
    size_bytes bigint,         -- the ENCRYPTED file's size, as actually uploaded
    error text
);

comment on table backup_jobs is
    'History of every database backup attempt (Backup & DR Phase 1, 2026-09) — not tenant data, no RLS; read only via a platform-admin-gated route.';
