-- ============================================================
-- AusTender OCDS API (Australia) — ninth real ingestion source.
--
-- WHY THIS ONE: found while researching Brazil/Chile as candidate
-- "new" sources (2026-09) — both of those turned out genuinely
-- blocked (Chile's live API needs a Clave Única, Chile's national
-- digital ID, to even request a ticket; Brazil's PNCP open-tender
-- endpoint failed or timed out on every live attempt with a
-- non-empty date range, though its separate /contratos endpoint
-- does work). AusTender was found during that same research pass and
-- is a clean win by comparison.
--
-- Verified live before building (2026-09-17), not assumed:
--   * https://api.tenders.gov.au/ocds/findByDates/contractPublished/{from}/{to}
--     — genuinely NO auth/key needed, confirmed with a bare curl.
--   * Real OCDS 1.1 shape, cursor pagination via response.links.next
--     (same pattern already used for UK Find a Tender / South Africa).
--   * Every contract's item(s) carry a real UNSPSC classification code
--     (release.contracts[].items[].classification, scheme "UNSPSC") —
--     this feeds taxonomy_unspsc_mapping's existing longest-prefix
--     match unchanged, same as CanadaBuys and Colombia. No sentinel
--     code needed, unlike India/South Africa.
--   * A live sample of 100 contract notices (2026-06-01 to 2026-09-10)
--     had 55 from "Department of Defence" as procuringEntity — a very
--     high hit rate compared to every other buyer-filtered source
--     here — with genuinely materiel-relevant descriptions ("Pump
--     Repair", "Battery Charger Repairs", "Transformer Repairs").
--   * Real supplier (winner) names, supplier ABN identifiers, contract
--     value and signing date are all present inline — this is the
--     SIXTH source able to populate contract_awards (after TED, UK
--     Find a Tender, Colombia, ProZorro, CanadaBuys), and does so on
--     every single row rather than a subset, since this feed IS
--     contract notices, not a pre-award tender pipeline.
--   * The buying party's `address` and `contactPoint` fields exist in
--     the schema but were found LIVE, across every Department of
--     Defence row sampled, to be an empty object and a single shared
--     department-wide inbox ("tenders@finance.gov.au") respectively —
--     never anything specific to the buying unit. Deliberately NOT
--     stored in programmes.contact_* as a result: showing the
--     byte-identical "contact" on every programme from this source
--     would look like fabricated data, not a real lead. See
--     app/australia_normalize.py.
--
-- A REAL, STATED LIMITATION: this endpoint is contractPublished only
-- — post-award contract notices, not open/pre-award tenders. Every
-- programme from this source is therefore ingested directly as
-- programmes.stage = 'contract_awarded', the same honest choice
-- CanadaBuys's separate award-file phase and Brazil's /contratos
-- endpoint would have needed. AusTender does also publish "business
-- opportunities" (pre-award) and "annual procurement plans" on the
-- www.tenders.gov.au portal itself, but no equivalent open API for
-- those was found or verified live — left as a stated gap, not
-- guessed at, same discipline as every other source's documented
-- gaps in this file.
--
-- Only "Department of Defence" was confirmed live as a materiel-
-- relevant procuring entity in the sampled window. Other genuinely
-- defence-related Australian bodies (e.g. the Australian Submarine
-- Corporation, Defence Science and Technology Group) may also appear
-- under their own party names but were not seen in the sample and are
-- deliberately NOT added to the buyer filter — the same "don't guess
-- a name that wasn't actually observed" discipline documented for
-- Colombia and South Africa's own buyer-phrase lists.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/038_australia_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'AusTender (Australian Government)',
    'government_portal',
    'high',
    'https://api.tenders.gov.au/ocds/findByDates/contractPublished/',
    'Official Australian Government procurement data, published on AusTender (the Commonwealth procurement information system) via its OCDS-compliant API. '
    || 'ACCESS: no API key required — confirmed live with an unauthenticated request. Cursor-based pagination (response.links.next), same shape as UK Find a Tender and eTenders South Africa. '
    || 'CLASSIFICATION: UNSPSC, on every contract line item (contracts[].items[].classification). Stored in programmes.naics_code and picked up unchanged by the existing UNSPSC longest-prefix matching used for CanadaBuys and Colombia. A contract with several items stores only the FIRST item''s code, the same single-category-per-row simplification Colombia already uses (codigo_principal_de_categoria). '
    || 'SCOPE: this endpoint is CONTRACT NOTICES ONLY (post-award), not a pre-award open-tender pipeline — every programme from this source is ingested at stage = contract_awarded. AusTender does publish pre-award "business opportunities" on its own portal, but no equivalent open API for that was found or verified; left as a stated gap. '
    || 'AWARDS: the winning supplier is published inline on every row (not a subset needing a guard, unlike Colombia''s "No Definido" case), making this the sixth source able to populate contract_awards. '
    || 'RELEVANCE: decided by procuring entity name. Only "Department of Defence" was confirmed live as a materiel-relevant buyer in the sampled window (55 of 100 sampled contract notices, genuinely defence-relevant: pump/battery/transformer repairs etc.) — see db/migrations/038 header and app/australia_normalize.py for what was and was not verified before being added to the filter.'
where not exists (
    select 1 from sources where name = 'AusTender (Australian Government)'
);
