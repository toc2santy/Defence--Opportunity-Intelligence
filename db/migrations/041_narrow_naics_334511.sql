-- ============================================================
-- Narrows NAICS 334511's mapping from 11 capabilities down to the
-- 3 genuinely grounded in its own official definition — a real
-- data-quality bug found live (2026-09), not a hypothetical one.
--
-- HOW IT WAS FOUND: a user checking the new Sector Coverage feature
-- (2026-09) noticed the exact same opportunity count (994) appearing
-- under seven unrelated sectors — ISR, Naval Systems, Electronic
-- Warfare, C-UAS, C4ISR, Sensors, Radar. Traced to a single root
-- cause: 994 of that tenant's matched programmes all carry the SAME
-- classification code, NAICS 334511, and that ONE code was mapped to
-- 11 DIFFERENT capabilities across 11 DIFFERENT sectors — so every
-- one of those 994 programmes counted toward all 11 sectors at once,
-- not because they were genuinely relevant to all 11, but because the
-- mapping table said so.
--
-- WHY THIS HAPPENED: this mapping dates to migration 004, this
-- project's original "curated v1 starting set, not exhaustive" (see
-- CLAUDE.md's own description of that era) — an early, broad-strokes
-- seeding that treated "sensor/instrument-adjacent" as license to
-- attach a code to nearly every sensor-adjacent capability in the
-- taxonomy. Migration 031 (this session) even added a 12th mapping
-- (SONAR.PASSIVE) on top of it, explicitly reasoning "everyone else
-- already accepts this imprecision" — a rationalization that, now
-- that a real user has surfaced the actual consequence, doesn't hold
-- up. That SONAR.PASSIVE mapping is one of the three kept below, on
-- its own separate (correct) merits.
--
-- THE OFFICIAL DEFINITION (US Census Bureau NAICS 334511 —
-- "Search, Detection, Navigation, Guidance, Aeronautical, and
-- Nautical System and Instrument Manufacturing"): "establishments
-- primarily engaged in manufacturing search, detection, navigation,
-- guidance, aeronautical, and nautical systems and instruments...
-- aircraft instruments (except engine), flight recorders,
-- navigational instruments and systems, RADAR systems and
-- equipment, and SONAR systems and equipment." Explicitly names
-- radar and sonar; general search/detection instruments genuinely
-- covers a general sensors capability. It does NOT name — and gives
-- no real basis for — UAV platforms, counter-UAS, electronic warfare
-- (a countermeasures technology, not a detection one), C4ISR
-- integration (a systems-of-systems capability), electro-optics (a
-- distinct sensing technology not named), AI/autonomy (a software
-- capability), or robotics (a distinct platform category). Those 8
-- capabilities may well have OTHER, correctly-scoped NAICS codes
-- mapped elsewhere in this table (unaffected by this migration) —
-- this only removes their claim on 334511 specifically.
--
-- Kept, on the definition's own words:
--   SENSING.RADAR     (radar systems and equipment — named directly)
--   SONAR.PASSIVE      (sonar systems and equipment — named directly)
--   SENSORS.GENERAL    (search/detection instruments — the general case)
--
-- NOT applied to NAICS 541712 (Research and Development in the
-- Physical, Engineering and Life Sciences), the platform's other
-- multi-capability NAICS code (6 capabilities) — genuinely different
-- in kind: 541712 classifies the ACTIVITY of doing R&D, not WHAT is
-- being researched, so NAICS alone cannot narrow it further no
-- matter how it is read. That breadth is a real, accepted limit of
-- the classification scheme itself (see migration 031's own
-- reasoning), not a seeding mistake — left untouched.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/041_narrow_naics_334511.sql
-- ============================================================

delete from taxonomy_naics_mapping
where naics_code = '334511'
  and capability_id in (
    select id from capability_taxonomy
    where code in (
        'UAV.INTEGRATION', 'EW.GENERAL', 'C4ISR.INTEGRATION',
        'SENSOR.ELECTRO_OPTIC', 'AUTONOMY.GENERAL', 'CUAS.GENERAL',
        'ISR.GENERAL', 'AUTONOMY.ROBOTICS'
    )
  );
