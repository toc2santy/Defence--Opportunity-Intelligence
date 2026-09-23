-- ============================================================
-- taxonomy_cpv_mapping v2 — from 22 rows to full coverage of the
-- defence CPV branch.
--
-- WHY: award winners are extracted from EU TED and UK Find a Tender
-- only, and both publish CPV. With 22 mapping rows, whole capabilities
-- (UAV.INTEGRATION, COMMS.SECURE.TACTICAL, SONAR.PASSIVE,
-- C4ISR.INTEGRATION, AEROSPACE.COMPONENTS, SPACE.DEFENCE,
-- AUTONOMY.*) had NO CPV row at all, so Competitor Intelligence and
-- Partner Matching could not answer for a product in those areas —
-- and, worse, reported the silence as "no competitors found" rather
-- than "cannot join". This closes that gap.
--
-- HOW THE CODES WERE VERIFIED, rather than recalled: TED publishes
-- the official CPV label inside the notice title itself, in the form
-- "Belgium – Unmanned aerial vehicles – <subject>". Every code below
-- was read back out of our OWN ingested TED rows together with the
-- label TED printed for it, e.g.
--
--   35613000 → "Unmanned aerial vehicles"      (13 live notices)
--   35712000 → "Tactical command, control and communication systems"
--   38113000 → "Sonars"
--   35730000 → "Electronic warfare systems and counter measures"
--   35722000 → "Radar"  ·  35723000 → "Air defence radar"
--
-- so the mapping is grounded in the publisher's own labelling of
-- notices we actually hold, not in a remembered code list. Two codes
-- are the exception and are marked inline.
--
-- KNOWN ISSUE DELIBERATELY NOT CHANGED HERE: seven capabilities
-- (CUAS, CYBER.DEFENCE, EW, ISR, SENSING.RADAR, SENSOR.ELECTRO_OPTIC,
-- SENSORS.GENERAL) all share the generic 35120000 "Surveillance and
-- security systems and devices" from v1. That means one CCTV tender
-- scores a +3 category bonus against all seven at once — the same
-- class of false positive the "GPS HATCH SYSTEMS" incident produced.
-- Now that each of those capabilities has precise codes below, those
-- generic rows are candidates for removal, but removing them changes
-- matching results platform-wide, so it is left as an explicit,
-- separate decision rather than folded into an additive migration.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/025_cpv_mapping_expansion.sql
-- ============================================================

insert into taxonomy_cpv_mapping (capability_id, cpv_code)
select ct.id, m.cpv_code
from (values
    -- Aerospace components — parts, structures, engines
    ('AEROSPACE.COMPONENTS', '35640000'),   -- Parts for military aerospace equipment
    ('AEROSPACE.COMPONENTS', '35641000'),   -- Structure and mechanical spare parts for military aerospace equipment
    ('AEROSPACE.COMPONENTS', '35641100'),   -- Engines and engine parts for military aerospace equipment

    -- Military aviation — the airframes themselves
    ('AVIATION.MILITARY',    '35610000'),   -- Military aircrafts
    ('AVIATION.MILITARY',    '35611100'),   -- Fighter aircrafts
    ('AVIATION.MILITARY',    '35611400'),   -- Military transport aircrafts
    ('AVIATION.MILITARY',    '35611500'),   -- Training aircrafts

    -- Autonomy — air and sub-surface unmanned platforms
    ('AUTONOMY.GENERAL',     '35613000'),   -- Unmanned aerial vehicles
    ('AUTONOMY.GENERAL',     '35512400'),   -- Unmanned underwater vehicles
    ('AUTONOMY.GENERAL',     '34711200'),   -- Non-piloted aircraft
    ('AUTONOMY.ROBOTICS',    '35512400'),
    ('AUTONOMY.ROBOTICS',    '34711200'),

    -- Tactical UAV
    ('UAV.INTEGRATION',      '35613000'),
    ('UAV.INTEGRATION',      '34711200'),

    -- C4ISR integration
    ('C4ISR.INTEGRATION',    '35700000'),   -- Military electronic systems
    ('C4ISR.INTEGRATION',    '35710000'),   -- Command, control, communication and computer systems
    ('C4ISR.INTEGRATION',    '35711000'),   -- Command, control, communication systems
    ('C4ISR.INTEGRATION',    '35712000'),   -- Tactical command, control and communication systems
    ('C4ISR.INTEGRATION',    '35720000'),   -- Intelligence, surveillance, target acquisition and reconnaissance

    -- Secure tactical communication
    ('COMMS.SECURE.TACTICAL','35712000'),
    ('COMMS.SECURE.TACTICAL','35711000'),
    ('COMMS.SECURE.TACTICAL','32570000'),   -- Communications equipment
    ('COMMS.SECURE.TACTICAL','32573000'),   -- Communications control system
    ('COMMS.SECURE.TACTICAL','32500000'),   -- Telecommunications equipment and supplies

    -- Passive sonar / acoustic detection
    ('SONAR.PASSIVE',        '38113000'),   -- Sonars
    ('SONAR.PASSIVE',        '32342400'),   -- Acoustic devices

    -- Space
    ('SPACE.DEFENCE',        '35631200'),   -- Observation satellites
    ('SPACE.DEFENCE',        '35631300'),   -- Navigation satellites
    ('SPACE.DEFENCE',        '32533000'),   -- Satellite earth stations

    -- Electronic warfare
    ('EW.GENERAL',           '35730000'),   -- Electronic warfare systems and counter measures
    ('EW.GENERAL',           '35700000'),

    -- Radar
    ('SENSING.RADAR',        '35722000'),   -- Radar
    ('SENSING.RADAR',        '35723000'),   -- Air defence radar
    ('SENSING.RADAR',        '32352200'),   -- Radar spare parts and accessories
    ('SENSING.RADAR',        '35121900'),   -- Radar detectors

    -- ISR
    ('ISR.GENERAL',          '35720000'),
    ('ISR.GENERAL',          '35721000'),   -- Electronic intelligence system
    ('ISR.GENERAL',          '35125000'),   -- Surveillance system

    -- Electro-optic sensors
    ('SENSOR.ELECTRO_OPTIC', '38631000'),   -- Binoculars
    ('SENSOR.ELECTRO_OPTIC', '38635000'),   -- Telescopes
    ('SENSOR.ELECTRO_OPTIC', '38636000'),   -- Specialist optical instruments
    ('SENSOR.ELECTRO_OPTIC', '38651000'),   -- Cameras

    -- General sensors
    ('SENSORS.GENERAL',      '35125100'),   -- Sensors
    ('SENSORS.GENERAL',      '35125110'),   -- Biometric sensors
    ('SENSORS.GENERAL',      '30237475'),   -- Electric sensors

    -- Counter-UAS
    ('CUAS.GENERAL',         '35322100'),   -- Anti-aircraft
    ('CUAS.GENERAL',         '35723000'),
    ('CUAS.GENERAL',         '35730000'),

    -- Missiles and precision
    ('MISSILES.PRECISION',   '35620000'),   -- Missiles
    ('MISSILES.PRECISION',   '35622600'),   -- Anti-tank guided missiles
    ('MISSILES.PRECISION',   '35342000'),   -- Parts of rocket launchers

    -- Naval
    ('NAVAL.SYSTEMS',        '35511000'),   -- Surface combatant
    ('NAVAL.SYSTEMS',        '35512400'),
    ('NAVAL.SYSTEMS',        '35513200'),   -- Auxiliary research vessel
    ('NAVAL.SYSTEMS',        '35520000'),   -- Parts for warships
    ('NAVAL.SYSTEMS',        '35521000'),   -- Hull and mechanical spare parts for warships
    ('NAVAL.SYSTEMS',        '35521100'),   -- Engines and engine parts for warships
    ('NAVAL.SYSTEMS',        '35522000'),   -- Electronic and electrical spare parts for warships

    -- Land systems
    ('LAND.SYSTEMS',         '35412100'),   -- Infantry fighting vehicles
    ('LAND.SYSTEMS',         '35412200'),   -- Armoured personnel carriers
    ('LAND.SYSTEMS',         '35412500'),   -- Command and liaison vehicles
    ('LAND.SYSTEMS',         '35420000'),   -- Parts of military vehicles
    ('LAND.SYSTEMS',         '35421000'),   -- Mechanical spare parts for military vehicles

    -- Defence manufacturing
    ('MANUFACTURING.DEFENCE','35320000'),   -- Firearms
    ('MANUFACTURING.DEFENCE','35330000'),   -- Ammunition
    ('MANUFACTURING.DEFENCE','35340000'),   -- Parts of firearms and ammunition
    ('MANUFACTURING.DEFENCE','35341000'),   -- Parts of light firearms

    -- MRO
    ('MRO.GENERAL',          '50600000'),   -- Repair and maintenance of security and defence materials
    ('MRO.GENERAL',          '50610000'),   -- Repair and maintenance of security equipment
    ('MRO.GENERAL',          '50842000'),   -- Repair and maintenance of weapon systems

    -- The only two codes below NOT observed in our own ingested rows.
    -- Both are standard CPV 2008 entries for security software, and
    -- they are the precise alternative to CYBER.DEFENCE's inherited
    -- generic 35120000 (a CCTV code). Kept deliberately narrow: the
    -- broad 48000000/72000000 IT codes were considered and rejected,
    -- because they would make every office software tender a cyber
    -- defence match.
    ('CYBER.DEFENCE',        '48730000'),   -- Security software package
    ('CYBER.DEFENCE',        '72212730')    -- Security software development services
) as m(capability_code, cpv_code)
join capability_taxonomy ct on ct.code = m.capability_code
on conflict do nothing;
