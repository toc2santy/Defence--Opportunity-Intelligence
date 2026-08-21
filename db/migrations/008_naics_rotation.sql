-- ============================================================
-- NAICS rotation state — the actual fix for a real gap: the
-- scheduled ingestion has been running daily (when SAM_GOV_API_KEY
-- is set) but always pulling the SAME first 3 NAICS codes every
-- single time, since run_sam_gov_ingestion's default was a static
-- slice, not a rotating one. This gives it real memory of which
-- group it used last, so successive runs genuinely widen coverage
-- instead of repeatedly re-fetching the same narrow slice.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/008_naics_rotation.sql
-- ============================================================

create table if not exists ingestion_rotation_state (
    id              uuid primary key default gen_random_uuid(),
    source_name     text not null unique,
    rotation_index  integer not null default 0,
    updated_at      timestamptz not null default now()
);

insert into ingestion_rotation_state (source_name, rotation_index)
select 'SAM_GOV_NAICS_ROTATION', 0
where not exists (
    select 1 from ingestion_rotation_state where source_name = 'SAM_GOV_NAICS_ROTATION'
);
