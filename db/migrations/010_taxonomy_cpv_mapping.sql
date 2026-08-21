-- ============================================================
-- CPV-to-taxonomy mapping — closes the real gap flagged when UK
-- Find a Tender was added: real UK data was flowing in, but
-- nothing connected it to Phase 3 matching, since
-- taxonomy_naics_mapping only knows NAICS codes.
--
-- Same honesty standard as the NAICS mapping: only capabilities
-- with a genuinely defensible, verified CPV code get one. Several
-- real capabilities — including UAV.INTEGRATION, this whole
-- project's own flagship example — are deliberately left
-- UNMAPPED below, because no verified UAV-specific CPV code was
-- found. A weak guess would be worse than an honest gap.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/010_taxonomy_cpv_mapping.sql
-- ============================================================

create table taxonomy_cpv_mapping (
    id              uuid primary key default gen_random_uuid(),
    capability_id   uuid not null references capability_taxonomy(id) on delete cascade,
    cpv_code        text not null,
    unique (capability_id, cpv_code)
);

-- CPV codes here are the same ones verified against multiple
-- independent procurement-classification sources when UK Find a
-- Tender was built (see app/uk_ft_normalize.py). Nothing new is
-- introduced here that wasn't already checked.
insert into taxonomy_cpv_mapping (capability_id, cpv_code)
select ct.id, m.cpv_code
from capability_taxonomy ct
join (values
    -- Naval — direct, high-confidence fit
    ('NAVAL.SYSTEMS', '35500000'),   -- Warships and associated parts
    ('NAVAL.SYSTEMS', '35510000'),   -- Warships
    ('NAVAL.SYSTEMS', '50640000'),   -- Repair/maintenance of warships

    -- Land systems — direct fit
    ('LAND.SYSTEMS', '35400000'),    -- Military vehicles and associated parts
    ('LAND.SYSTEMS', '35410000'),    -- Armoured military vehicles
    ('LAND.SYSTEMS', '50630000'),    -- Repair/maintenance of military vehicles

    -- Missiles/precision — direct fit
    ('MISSILES.PRECISION', '35300000'),  -- Weapons, ammunition and associated parts
    ('MISSILES.PRECISION', '35310000'),  -- Miscellaneous weapons

    -- Surveillance/sensing-adjacent capabilities — mapped to the
    -- one verified surveillance/security CPV code; a reasonable
    -- fit, not a perfect one, and stated as such
    ('EW.GENERAL', '35120000'),
    ('SENSOR.ELECTRO_OPTIC', '35120000'),
    ('SENSORS.GENERAL', '35120000'),
    ('CUAS.GENERAL', '35120000'),
    ('ISR.GENERAL', '35120000'),
    ('SENSING.RADAR', '35120000'),
    ('CYBER.DEFENCE', '35120000'),   -- weaker fit — cyber defence services likely sit under
                                      -- IT-services CPV codes (72xxxxxx), not yet researched/verified

    -- Aviation — repair/maintenance only; no verified CPV code
    -- found for military aircraft PROCUREMENT itself
    ('AVIATION.MILITARY', '50650000'),

    -- MRO — strong fit across all four verified repair codes
    ('MRO.GENERAL', '50630000'),
    ('MRO.GENERAL', '50640000'),
    ('MRO.GENERAL', '50650000'),
    ('MRO.GENERAL', '50660000'),

    -- Defence manufacturing — broad fit
    ('MANUFACTURING.DEFENCE', '35300000'),
    ('MANUFACTURING.DEFENCE', '35400000')
) as m(code, cpv_code) on m.code = ct.code
on conflict (capability_id, cpv_code) do nothing;

-- Deliberately NOT mapped yet (real gap, not an oversight):
-- UAV.INTEGRATION, C4ISR.INTEGRATION, COMMS.SECURE.TACTICAL,
-- AUTONOMY.GENERAL, AUTONOMY.ROBOTICS, SPACE.DEFENCE,
-- AEROSPACE.COMPONENTS — no CPV code verified specific enough to
-- defend for these during this research pass.
