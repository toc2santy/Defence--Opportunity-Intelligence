-- ============================================================
-- UK Find a Tender Service — second real ingestion source, proving
-- the generalized registry actually works for a genuinely different
-- kind of source (no API key, no server-side category filter,
-- client-side CPV filtering instead of NAICS rotation).
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/009_uk_find_a_tender.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'UK Find a Tender Service',
    'government_portal',
    'high',
    'https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages',
    'Official UK government public API (OCDS format), published under the Open Government Licence v3.0. No API key required — confirmed from GOV.UK''s own API documentation, which shows no authentication for this read endpoint. No server-side category (CPV) filter exists on this endpoint; defense-relevance is filtered client-side after fetching. The API enforces its own dynamic rate limit (HTTP 429 with a Retry-After header) — no fixed daily quota is documented, unlike SAM.gov''s stated ~10/day for personal keys.'
where not exists (
    select 1 from sources where name = 'UK Find a Tender Service'
);
