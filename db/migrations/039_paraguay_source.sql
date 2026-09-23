-- ============================================================
-- DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)
-- — tenth real ingestion source, found while researching Latin
-- American candidates alongside Mexico and Peru (2026-09).
--
-- WHY THIS ONE, not Mexico or Peru: all three were assessed live.
-- Mexico's documented API (api.datos.gob.mx/v1/contratacionesabiertas)
-- genuinely timed out on every live attempt (confirmed via a control
-- request against a known-working API in the same session, ruling
-- out a local network problem). Peru's OECE (formerly OSCE) API and
-- bulk-download domain both returned a live HTTP 403 Forbidden.
-- Paraguay's v3 API answered immediately with real data and needed
-- no registration at all for read access, despite its own Swagger
-- spec declaring a global Bearer Auth requirement — verified live,
-- not assumed from the docs (this project's own SICP account
-- registration flow does exist, self-service via email or Google/
-- GitHub, should the unauthenticated rate ever become limiting).
--
-- Verified live before building (2026-09-18):
--   * Base: https://www.contrataciones.gov.py/datos/api/v3/doc
--     (the "/doc" segment is genuinely part of the real API path,
--     not just the Swagger UI — confirmed by successfully calling
--     .../doc/search/processes and .../doc/ocds/record/{ocid}).
--   * /search/processes takes fecha_desde/fecha_hasta/tipo_fecha,
--     tender.procuringEntity.name, page/items_per_page — a genuine
--     server-side search, not a bulk dump.
--   * Item classification (tender.items[].classification, scheme
--     "catalogoNivel5DNCP") is UNSPSC with a local DNCP suffix, e.g.
--     "23151607-001" — the leading 8 digits ARE the real UNSPSC code
--     (confirmed: 23151607 = "Prensa" / hydraulic press, a real
--     UNSPSC 8-digit commodity code), so this plugs into the existing
--     taxonomy_unspsc_mapping longest-prefix walk unchanged, the same
--     as CanadaBuys, Colombia and Australia — the suffix is stripped
--     on ingest.
--   * Award winners are published inline (awards[].suppliers[].name),
--     confirmed live against a real awarded Armada Paraguaya record
--     (supplier: "Helisul Táxi Aéreo Ltda") — the SEVENTH source able
--     to populate contract_awards.
--   * A live sample under "Ministerio de Defensa Nacional" turned up
--     genuinely materiel-relevant procurement carrying real US
--     MIL-SPEC part numbers (e.g. item description "EMPAQUE
--     MS28775-011", country-of-origin attribute "USA") — MS28775 is
--     a real aerospace/military O-ring specification, not a
--     coincidental string match.
--
-- A REAL, LIVE-CONFIRMED ENGINEERING TRAP, different from every
-- other source's: this API's own responses are INTERMITTENTLY
-- TRUNCATED MID-JSON on a genuine HTTP 200 — confirmed repeatedly,
-- worse at larger items_per_page but not eliminated even at 5-10.
-- This is not a timeout or a disconnect (both already-solved problem
-- shapes from CPPP and South Africa) — the connection completes
-- normally and the body is simply cut off part-way through an object,
-- so a naive parser gets a confusing JSONDecodeError rather than a
-- clean failure signal. app/paraguay_ingestion.py treats a JSON parse
-- failure as a retryable condition (small backoff, re-request the
-- SAME page), the same spirit as CPPP's reconnect-and-resume but for
-- a corrupted-body failure mode instead of a dropped connection.
--
-- RELEVANCE (same shape of problem as Colombia, different specifics):
-- buyer filtering alone is not enough — a live sample under
-- "Ministerio de Defensa Nacional" mixed real materiel (vehicle
-- parts, military vehicles, aircraft parts, arms-registry supplies)
-- with generic institutional procurement (kitchen utensils, office
-- folders, firewall software licences, building maintenance, food
-- supply contracts, photocopier rental, insurance). See
-- app/paraguay_normalize.py's NON_MATERIEL_CATEGORY_PHRASES for the
-- category-level exclusion this needs, built from what was actually
-- observed rather than guessed. Spanish keyword corroboration already
-- built for Colombia (db/migrations/028) directly helps disambiguate
-- here too, since both feeds are in Spanish.
--
-- A SECOND ENGINEERING TRAP, found by live-testing the classification
-- fetch rather than trusting an earlier manual read: /search/processes'
-- own compiledRelease carries NO items at all, so classification codes
-- need a second, per-candidate GET /ocds/record/{ocid} call — but that
-- endpoint's real shape is a RECORD package
-- ({"records": [{"ocid", "releases", "compiledRelease"}]}), not a bare
-- release package. The first implementation assumed the latter and
-- silently got zero codes on a genuinely live, 200-OK, correctly-
-- parsed response — caught only by testing the function against a
-- real fetch, not by the HTTP layer (which reported success). Each
-- item also carries classification TWICE — DNCP's own
-- "catalogoNivel5DNCP" scheme (code+local suffix) AND a genuine bare
-- "UNSPSC" entry under additionalClassifications — the latter is
-- preferred since it needs no suffix stripping. See
-- app/paraguay_normalize.py's extract_classification_from_detail.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/039_paraguay_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)',
    'government_portal',
    'high',
    'https://www.contrataciones.gov.py/datos/api/v3/doc',
    'Official Paraguayan public procurement data, published by the Dirección Nacional de Contrataciones Públicas (DNCP) via a REST + OCDS v1.1 API. '
    || 'ACCESS: no registration required for the read/search endpoints used here — confirmed live despite the API''s own Swagger spec declaring a global Bearer Auth requirement; a self-service account (email or Google/GitHub sign-in) exists at /datos/adm/login should the unauthenticated rate ever need raising. '
    || 'CLASSIFICATION: UNSPSC, published under scheme "catalogoNivel5DNCP" as an 8-digit UNSPSC code plus a local DNCP suffix (e.g. "23151607-001") — the suffix is stripped on ingest and the 8-digit code stored in programmes.naics_code, picked up unchanged by the existing UNSPSC longest-prefix matching used for CanadaBuys, Colombia and Australia. '
    || 'AWARDS: the winning supplier is published inline (awards[].suppliers[].name) on awarded processes, making this the seventh source able to populate contract_awards. '
    || 'RELEVANCE: decided by procuring entity (Ministerio de Defensa Nacional and its named commands) AND by procurement category, since buyer filtering alone mixes real materiel with generic institutional procurement (food, furniture, office supplies, insurance, building maintenance) — see app/paraguay_normalize.py and this migration''s own header for what was found in live data. '
    || 'KNOWN ENGINEERING TRAP: this API''s responses are intermittently truncated mid-JSON on an otherwise-normal HTTP 200 — handled with a parse-failure retry in app/paraguay_ingestion.py, not a timeout or disconnect issue.'
where not exists (
    select 1 from sources where name = 'DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)'
);
