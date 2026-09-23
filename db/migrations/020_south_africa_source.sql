-- ============================================================
-- eTenders South Africa (National Treasury) — sixth real ingestion
-- source, and the first covering Africa.
--
-- Researched and verified live before building (Middle East was
-- assessed alongside this and rejected — Saudi Etimad, UAE eSupply/
-- DGS, Israel mr.gov.il and the Jordan/Egypt/Kuwait portals all
-- publish no open bulk API or dataset; the only "data" reachable
-- there is via paid third-party aggregators, not a primary
-- government source, the same class of problem already rejected for
-- GeM). South Africa's own portal is a genuine open government API
-- with no such issue.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/020_south_africa_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'eTenders South Africa (National Treasury)',
    'government_portal',
    'high',
    'https://ocds-api.etenders.gov.za/api/OCDSReleases',
    'Official Government of South Africa procurement portal, operated by National Treasury. Publication policy states data is published for reuse under the Open Contracting Data Standard (OCDS). '
    || 'ACCESS: no API key required. Real public JSON REST API (ocds-api.etenders.gov.za, swagger at /swagger) with dateFrom/dateTo/PageNumber/PageSize query params and a links.next cursor for pagination — same OCDS shape as UK Find a Tender. '
    || 'CLASSIFICATION: no CPV/NAICS/UNSPSC-equivalent code — tender.category is free text ("Services: Building", "Goods"), not a structured scheme. Defence-relevance is therefore determined by the buying/procuring entity, same approach as CPPP (India), and matched rows are stored with the sentinel code ZA-DEF — see app/south_africa_normalize.py. '
    || 'VERIFIED LIVE before building: ARMSCOR (Armaments Corporation of South Africa, the state defence acquisition agency) appears as a real procuringEntity/buyer in the feed with genuine solicitation numbers, deadlines and a named procurement contact. Volume is modest — roughly 1-2 defence-tagged releases per ~100 general releases in initial sampling, comparable in order of magnitude to CPPP.'
where not exists (
    select 1 from sources where name = 'eTenders South Africa (National Treasury)'
);
