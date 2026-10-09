-- ============================================================
-- Automatic data-health checks (2026-10).
--
-- Why: ~700 code tests did not catch the data-MEANING bugs found by using the
-- product on real tenders — SECOP II tenders already awarded but shown as open
-- (28% of them), Paraguay "Adjudicada" never recognised (42% of its open
-- tenders), missing closing dates, links a portal refuses. Those are only
-- visible by checking stored data against its own invariants and against the
-- source itself, so that is now done by the system, automatically: after every
-- scheduled ingestion, daily, and weekly against the source records.
--
-- One row per (run, source, check). Not tenant data, so no RLS (same as the
-- programmes/sources tables it describes). Purged after 180 days by the job
-- that writes it.
-- ============================================================

create table data_health_results (
    id          uuid primary key default gen_random_uuid(),
    run_id      uuid not null,
    checked_at  timestamptz not null default now(),
    trigger     text not null,
    source_name text,
    check_code  text not null,
    severity    text not null check (severity in ('ok', 'warn', 'error')),
    affected    integer not null default 0,
    total       integer not null default 0,
    message     text not null,
    detail      jsonb not null default '{}'::jsonb
);

create index idx_data_health_latest on data_health_results (source_name, check_code, checked_at desc);
create index idx_data_health_time on data_health_results (checked_at desc);
