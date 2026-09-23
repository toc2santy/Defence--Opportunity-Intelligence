-- ============================================================
-- CPPP (Central Public Procurement Portal, India) — fourth real
-- ingestion source, and the first covering India.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/014_cppp_india_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'CPPP (Central Public Procurement Portal, India)',
    'government_portal',
    'high',
    'https://eprocure.gov.in/cppp/latestactivetendersnew',
    'Government of India central procurement portal, operated by NIC. Under GFR 2017 all central government tenders above Rs 25 lakh must be published here, which makes it the mandatory disclosure point for a large share of Indian central procurement including Military Engineer Services, BSF, the service branches, DRDO and defence PSUs. '
    || 'ACCESS: no API key and no documented API of any kind — the active-tenders listing is parsed from HTML (10 rows per page). The portal rate-limits: light probing drew a refused connection, so ingestion fetches pages sequentially with a delay and a hard page cap. The /searchbyproduct route is captcha-protected and is deliberately NOT used. '
    || 'CLASSIFICATION: CPPP exposes no CPV/NAICS-equivalent code (its whole vocabulary is Goods/Services/Works, not surfaced on the listing). Defence-relevance is therefore determined by the publishing organisation, and matched rows are stored with the sentinel code IN-DEF — see app/cppp_india_normalize.py. '
    || 'COMPLIANCE REVIEW STILL OUTSTANDING: written permission has not been sought from NIC (support-eproc@nic.in) for reuse of listing content in a commercial product. Alternatives were assessed and rejected — GeM requires prior written permission from the GeM SPV to reproduce portal content, DEFPROC serves a captcha instead of data, and data.gov.in carries only aggregate procurement statistics (verified: zero catalog results for both "eprocurement" and "CPPP").'
where not exists (
    select 1 from sources where name = 'CPPP (Central Public Procurement Portal, India)'
);
