-- ============================================================
-- ProZorro (Ukraine) — eighth ingestion source, first in Eastern
-- Europe.
--
-- Verified live before building (2026-09-16), not assumed:
--   * public.api.openprocurement.org/api/2.5 — no key required.
--   * The list endpoint supports NO server-side filter of any kind:
--     `classification_id=35000000` and `opt_fields=classification`
--     were both sent and silently ignored (identical results with or
--     without them). This is a raw changes feed meant for building a
--     full local mirror, not a queryable dataset — the only source in
--     this project shaped that way. See app/prozorro_normalize.py and
--     app/prozorro_ingestion.py for the two-phase fetch this forces.
--   * A live 3,000-row sample of the most recent ~14 hours of
--     national activity found 681 rows (23%) from a defence
--     institution — the highest buyer hit-rate of any source here —
--     but the large majority was military units buying underwear,
--     potatoes and office laptops for internal use, not materiel.
--   * ДК021, this feed's classification scheme, IS the EU's CPV:
--     confirmed live — code "18310000-5" is printed with the label
--     "Спідня білизна" (Underwear), matching CPV 18310000 exactly;
--     "34130000-7" prints "Мототранспортні вантажні засоби", matching
--     CPV 34130000 (Motor lorries). Same numbers, translated label.
--   * procuringEntity.contactPoint (name/email/telephone) and
--     .address (street/locality/region/postalCode/country) are both
--     consistently populated, REQUIRED-looking fields on this feed —
--     more reliably present than on any other source assessed.
--   * Public notice pages at prozorro.gov.ua/tender/{tenderID} return
--     HTTP 200 for a real tenderID — a genuine, stable public link.
--
-- LICENSING: ProZorro states plainly that its data is open for reuse,
-- commercial use included (prozorro.gov.ua/about) — consistent with
-- its founding purpose as a public-transparency/anti-corruption
-- platform. The clearest reuse position of any source assessed for
-- this project, unlike GeM (India), rejected specifically for
-- requiring prior written permission for exactly this kind of use.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/030_prozorro_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'ProZorro (Ukraine Public Procurement)',
    'government_portal',
    'high',
    'https://public.api.openprocurement.org/api/2.5/tenders',
    'Official Ukrainian public procurement data, published by the ProZorro platform (Ministry of Economy / Transparency International Ukraine origin) under an open-data mandate — the platform states plainly that its data is open for reuse, commercial use included. '
    || 'ACCESS: no API key required. The list (sync) endpoint supports no server-side filter — confirmed live, classification/buyer query parameters are silently ignored — so this source is fetched as a two-phase list-scan-then-detail-fetch, unlike every other source here. See app/prozorro_ingestion.py. '
    || 'CLASSIFICATION: ДК021, confirmed live to be the EU''s CPV under a different name (same codes, translated labels), so it feeds the existing taxonomy_cpv_mapping directly with no new matching logic. '
    || 'RELEVANCE: a three-part test — defence-institution buyer, not a military health/welfare unit, and a materiel-relevant CPV code (reusing app.uk_ft_normalize.is_defense_relevant_cpv rather than a second drifting copy of the same list) — see app/prozorro_normalize.py for why buyer filtering alone is not enough on this feed (a live sample found the majority of defence-buyer rows were routine internal purchasing, not capability procurement). '
    || 'AWARDS: this feed publishes real winner data (awards[].suppliers, status-checked), making it the fourth source (after EU TED, UK Find a Tender and Colombia) able to populate contract_awards.'
where not exists (
    select 1 from sources where name = 'ProZorro (Ukraine Public Procurement)'
);
