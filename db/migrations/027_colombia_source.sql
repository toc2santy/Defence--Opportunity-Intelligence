-- ============================================================
-- SECOP II (Colombia) — seventh real ingestion source, and the
-- first covering Latin America.
--
-- WHY THIS ONE, out of everything assessed: it is the only candidate
-- found that publishes UNSPSC. That matters because this platform
-- already matches UNSPSC by longest-prefix walk for CanadaBuys
-- (taxonomy_unspsc_mapping), so Colombian tenders feed real
-- capability matching on day one — no sentinel code, unlike India
-- (IN-DEF) and South Africa (ZA-DEF), which publish no classification
-- scheme at all and can therefore only ever match on keywords.
--
-- Verified live before building (2026-09-16), not assumed:
--   * Socrata open-data API, no key: www.datos.gov.co/resource/p6dx-8zbt.json
--   * 549 rows from military buyers published since 2026-08-01, the
--     newest dated the day before this migration was written.
--   * codigo_principal_de_categoria carries UNSPSC with a "V1."
--     prefix — e.g. "V1.25172504" (tyres), whose 2517 family is
--     exactly the prefix shape taxonomy_unspsc_mapping stores.
--   * urlproceso is a nested object {"url": "..."} pointing at the
--     real SECOP II notice page.
--   * Award winners are published inline (nombre_del_proveedor,
--     nit_del_proveedor_adjudicado, valor_total_adjudicacion) — see
--     the ingestion module for the guard this needs.
--
-- THREE TRAPS, each found in live data and each handled in
-- app/colombia_normalize.py rather than discovered later in prod:
--
--  1. "DEFENSA" IN A NAME DOES NOT MEAN DEFENCE. Two of the largest
--     matches for that word are civilian bodies: "CORPORACIÓN
--     AUTÓNOMA REGIONAL PARA LA DEFENSA DE LA MESETA DE BUCARAMANGA"
--     (an environmental authority) and "AGENCIA NACIONAL DE DEFENSA
--     JURÍDICA DEL ESTADO" (the state's legal-defence agency).
--
--  2. MOST COLOMBIAN PUBLIC PROCUREMENT IS INDIVIDUAL SERVICE
--     CONTRACTS. Of 549 live military-buyer rows, 339 were
--     "Prestación de servicios" and their procedure name is often
--     just a person's name. Buyer filtering alone — which is what
--     CanadaBuys and CPPP use — would fill this platform with
--     contractor hiring records. Colombia therefore needs the buyer
--     test AND a goods test; see the normalizer.
--
--  3. MILITARY HEALTH AND WELFARE UNITS DOMINATE BY VOLUME.
--     Dispensarios médicos, hospitales navales, jefaturas de salud
--     and liceos (schools) buy medicines, lab reagents and teaching
--     services. They are genuinely military bodies, but this is not
--     capability procurement — the same judgement already applied to
--     MES estate works in India and CFB construction in Canada.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/027_colombia_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'SECOP II (Colombia Compra Eficiente)',
    'government_portal',
    'high',
    'https://www.datos.gov.co/resource/p6dx-8zbt.json',
    'Official Colombian public procurement data, published by Colombia Compra Eficiente on the national open-data portal datos.gov.co under the Socrata Open Data API. '
    || 'ACCESS: no API key required. An app token is optional and only raises the rate limit — anonymous requests are throttled and can return HTTP 429, which the ingestion layer treats as a retryable condition rather than a failure. '
    || 'CLASSIFICATION: UNSPSC, in codigo_principal_de_categoria, prefixed "V1." (e.g. V1.25172504). The prefix is stripped on ingest and the 8-digit code is stored in programmes.naics_code, where the existing UNSPSC longest-prefix matching used for CanadaBuys picks it up unchanged. Some rows carry "UNSPECIFIED" or a non-numeric value instead; those are stored without a classification code rather than guessed at. '
    || 'AWARDS: this feed publishes the winning supplier inline, so it is the third source (after EU TED and UK Find a Tender) able to populate contract_awards. Guarded: adjudicado = ''Si'' is not sufficient, because the supplier name is frequently the literal string ''No Definido''. '
    || 'RELEVANCE: decided by the buying entity AND by the procurement being for goods — see db/migrations/027 header and app/colombia_normalize.py for the three live-data traps behind that, which are specific to this country and not shared with any existing source.'
where not exists (
    select 1 from sources where name = 'SECOP II (Colombia Compra Eficiente)'
);
