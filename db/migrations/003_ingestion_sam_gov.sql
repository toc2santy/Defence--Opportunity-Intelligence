-- ============================================================
-- Phase 2 migration: real external data ingestion — SAM.gov.
--
-- Apply the same way as the Phase 1 migration:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/003_ingestion_sam_gov.sql
-- ============================================================

-- programmes had no way to detect "we already ingested this
-- exact record" — re-running ingestion would have silently
-- duplicated every row. Add an external reference + a unique
-- constraint scoped per source, so ingestion can be a safe
-- upsert instead of a blind insert.
alter table programmes add column if not exists external_ref text;
create unique index if not exists idx_programmes_source_external_ref
    on programmes (source_id, external_ref)
    where external_ref is not null;

-- The source record every SAM.gov-derived programme will cite.
insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'SAM.gov Contract Opportunities API',
    'government_portal',
    'high',
    'https://api.sam.gov/opportunities/v2/search',
    'Official U.S. federal government public API (open.gsa.gov). No restrictions on accessing or redistributing this public government information. Personal/non-federal API keys are rate-limited (approx. 10 requests/day) — ingestion jobs must budget calls accordingly, one call per NAICS code per run.'
where not exists (
    select 1 from sources where name = 'SAM.gov Contract Opportunities API'
);
