-- ============================================================
-- CanadaBuys (Government of Canada) — fifth real ingestion source,
-- and the first to bring a THIRD classification scheme (UNSPSC)
-- alongside NAICS (US) and CPV (UK/EU).
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/015_canada_buys_source.sql
-- ============================================================

insert into sources (name, source_type, trust_level, url, terms_notes)
select
    'CanadaBuys (Government of Canada)',
    'government_portal',
    'high',
    'https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv',
    'Official Government of Canada procurement portal, operated by PSPC. Published as an OPEN DATASET under the Open Government Licence - Canada, which explicitly permits reuse including commercial reuse with attribution. '
    || 'This is the first source with no outstanding permission question: unlike GeM (prior written permission required from the GeM SPV) and CPPP (permission not yet sought from NIC), reuse here is licensed up front. '
    || 'ACCESS: no API key, no rate limiting encountered, no pagination — one HTTPS GET returns the entire open-tender set as CSV (976 rows in a live pull, 824 carrying UNSPSC codes). Refreshed daily by the publisher. '
    || 'IMPORTANT: use openTenderNotice-ouvertAvisAppelOffres.csv (all currently-open tenders), NOT newTenderNotice-nouvelAvisAppelOffres.csv, which is only a rolling delta of notices added since the last 2-hourly refresh and returned as few as 5 rows in testing. '
    || 'CLASSIFICATION: UNSPSC, an international standard rather than a national scheme, which is why it earns its own mapping table rather than a sentinel. Codes are multi-valued in the feed, asterisk-prefixed and newline-separated. '
    || 'RELEVANCE: decided by the buying entity (Department of National Defence, Canadian Armed Forces, RCN/RCAF, Canadian Coast Guard, Defence Research) and by W-prefixed PSPC solicitation numbers, because verified live data shows defence tenders spread across many UNSPSC segments — filtering on segment alone would miss most of them.'
where not exists (
    select 1 from sources where name = 'CanadaBuys (Government of Canada)'
);


-- ------------------------------------------------------------
-- UNSPSC-to-taxonomy mapping.
--
-- MATCHED AS A PREFIX, NOT AN EXACT CODE — this is the real
-- difference from the NAICS and CPV tables, and it is deliberate.
-- UNSPSC is hierarchical by construction: 8 digits encode
-- Segment(2) Family(2) Class(2) Commodity(2). A tender carries a
-- specific commodity code such as 25172800, which no curated table
-- could ever enumerate exhaustively. Storing the family prefix
-- '2517' and matching by prefix is therefore the semantically
-- correct reading of the standard, not a shortcut.
-- See app/programme_matching.py for the matching side.
--
-- Same honesty standard as 010_taxonomy_cpv_mapping.sql: only
-- capabilities with a genuinely defensible mapping get one. Real
-- capabilities are deliberately left UNMAPPED below (listed at the
-- end) because no verified UNSPSC prefix was established for them.
-- A weak guess would be worse than an honest gap.
-- ------------------------------------------------------------

create table if not exists taxonomy_unspsc_mapping (
    id              uuid primary key default gen_random_uuid(),
    capability_id   uuid not null references capability_taxonomy(id) on delete cascade,
    unspsc_prefix   text not null,
    unique (capability_id, unspsc_prefix)
);

create index if not exists idx_taxonomy_unspsc_prefix
    on taxonomy_unspsc_mapping (unspsc_prefix);

insert into taxonomy_unspsc_mapping (capability_id, unspsc_prefix)
select ct.id, m.unspsc_prefix
from capability_taxonomy ct
join (values
    -- Segment 25 — Commercial and Military Vehicles, Ships, Aircraft
    ('LAND.SYSTEMS',          '2510'),  -- Motor vehicles
    ('LAND.SYSTEMS',          '2517'),  -- Vehicle components/systems
    ('NAVAL.SYSTEMS',         '2511'),  -- Marine transport / watercraft
    ('AVIATION.MILITARY',     '2513'),  -- Aircraft
    ('AEROSPACE.COMPONENTS',  '2513'),  -- Aircraft (component supply shares the family)

    -- Segment 46 — Defense and Law Enforcement and Security Equipment
    ('MISSILES.PRECISION',    '4610'),  -- Conventional weapons and ammunition
    ('MANUFACTURING.DEFENCE', '46'),    -- Whole defence-equipment segment

    -- Segment 43 — Information Technology, Broadcasting, Telecom
    ('COMMS.SECURE.TACTICAL', '4322'),  -- Data, voice and multimedia network equipment

    -- Segment 41 — Laboratory, Measuring, Observing and Testing Equipment
    ('SENSORS.GENERAL',       '4111'),  -- Measuring, observing and testing instruments
    ('SENSING.RADAR',         '4111'),
    -- Class-level, not family-level: 411155 is the acoustic/sound
    -- measuring class, confirmed against a live CanadaBuys tender
    -- "Multiple Victoria-Class Spares: HYDROPHONE, SONAR".
    ('SONAR.PASSIVE',         '411155'),

    -- Segment 81 — Engineering, Research and Technology Based Services
    ('CYBER.DEFENCE',         '8111')   -- Computer services
) as m(code, unspsc_prefix) on m.code = ct.code
on conflict (capability_id, unspsc_prefix) do nothing;

-- DELIBERATELY UNMAPPED, and why — these are honest gaps, not
-- oversights. Revisit only with a verified code, never a plausible
-- guess:
--   AUTONOMY.GENERAL, AUTONOMY.ROBOTICS — UNSPSC has no established
--     autonomous-systems family; the same gap CPV has for UAVs.
--   CUAS.GENERAL      — counter-UAS is newer than the classification.
--   EW.GENERAL        — electronic warfare has no distinct UNSPSC family.
--   ISR.GENERAL, C4ISR.INTEGRATION — cross-cutting capabilities that
--     span segments 41/43/46; no single prefix is defensible.
--   SPACE.DEFENCE     — satellite codes sit across segments 25 and 32
--     with no clean defence-space prefix.
--   MRO.GENERAL       — maintenance services are classified by the
--     thing maintained, not as a family of their own.
--   SENSOR.ELECTRO_OPTIC — would need a class-level code that was not
--     verified against live data the way SONAR.PASSIVE's was.
