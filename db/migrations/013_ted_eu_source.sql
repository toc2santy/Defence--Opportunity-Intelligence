-- ============================================================
-- EU TED (Tenders Electronic Daily) — third real ingestion source,
-- covering 27 EU member states + EEA countries in one single API.
-- No API key required for the Search API (confirmed from official
-- TED API v3 documentation).
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/013_ted_eu_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'EU TED (Tenders Electronic Daily)',
    'government_portal',
    'high',
    'https://api.ted.europa.eu/v3/notices/search',
    'Official EU procurement portal managed by the Publications Office of the European Union. Search API v3 allows anonymous access for published notices (no API key required — confirmed from official documentation). Covers all above-threshold procurement notices from 27 EU member states plus EEA countries. Uses CPV classification (same as UK Find a Tender). Server-side CPV filtering supported, unlike UK Find a Tender. Free reuse, commercial included, per Commission Decision 2011/833/EU.'
where not exists (
    select 1 from sources where name = 'EU TED (Tenders Electronic Daily)'
);
