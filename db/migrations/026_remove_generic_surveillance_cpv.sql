-- ============================================================
-- Removes the generic CPV 35120000 from the seven capabilities that
-- inherited it in the v1 mapping.
--
-- THE PROBLEM IT FIXES: 35120000 is "Surveillance and security
-- systems and devices" — the CCTV/alarms/access-control branch of
-- CPV, whose children are Security cameras (35125300), Alarm systems
-- (35121700), Metal detectors (35124000) and Biometric sensors
-- (35125110). In v1 it was mapped to CUAS.GENERAL, CYBER.DEFENCE,
-- EW.GENERAL, ISR.GENERAL, SENSING.RADAR, SENSOR.ELECTRO_OPTIC and
-- SENSORS.GENERAL all at once. One building-CCTV tender therefore
-- scored the +3 classification-code bonus against all seven
-- capabilities simultaneously — the same shape of false positive as
-- the "GPS HATCH SYSTEMS" case that put the low-confidence cap on
-- code-only matches in the first place.
--
-- WHY IT IS SAFE TO REMOVE NOW AND WAS NOT BEFORE: until
-- db/migrations/025, 35120000 was the ONLY CPV code several of these
-- capabilities had — removing it then would have left them unable to
-- match CPV-coded data at all. 025 gave each of them precise codes
-- drawn from TED's own published labels:
--
--   SENSING.RADAR        → 35722000 Radar, 35723000 Air defence radar
--   EW.GENERAL           → 35730000 Electronic warfare systems and counter measures
--   ISR.GENERAL          → 35720000 ISTAR, 35721000 Electronic intelligence system
--   SENSORS.GENERAL      → 35125100 Sensors, 35125110 Biometric sensors
--   SENSOR.ELECTRO_OPTIC → 38631000/38635000/38636000/38651000 optics and cameras
--   CUAS.GENERAL         → 35322100 Anti-aircraft, 35723000, 35730000
--   CYBER.DEFENCE        → 48730000/72212730 security software
--
-- so each keeps CPV reach through codes that actually denote what it
-- does. Note that ISR is the least obvious of these: "surveillance"
-- reads like ISR in English, but in CPV the military ISR branch is
-- 35720000, while 35120000 is physical site security.
--
-- EXPECTED EFFECT, measured before applying: 77 ingested programmes
-- carry 35120000. Those stop granting these seven capabilities a
-- category-code bonus. They are NOT excluded from matching — a real
-- keyword overlap still matches them, at the honest keyword-only
-- confidence, which is what such a match deserves.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/026_remove_generic_surveillance_cpv.sql
-- ============================================================

delete from taxonomy_cpv_mapping m
using capability_taxonomy ct
where ct.id = m.capability_id
  and m.cpv_code = '35120000'
  and ct.code in (
    'CUAS.GENERAL', 'CYBER.DEFENCE', 'EW.GENERAL', 'ISR.GENERAL',
    'SENSING.RADAR', 'SENSOR.ELECTRO_OPTIC', 'SENSORS.GENERAL'
  );
