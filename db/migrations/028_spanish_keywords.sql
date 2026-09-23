-- ============================================================
-- Spanish keywords for capability_taxonomy_keywords.
--
-- WHY: with SECOP II (Colombia) ingested, programme titles now arrive
-- in Spanish. The keyword re-score in app/matching_scoring.py is what
-- turns a classification-code match into a corroborated one, and with
-- an English-only keyword set it scored every Colombian title at
-- zero — which caps the match at "low confidence" by design
-- (keyword_score == 0 → always low). So 292 real Colombian
-- programmes could be *found* but never *trusted*.
--
-- GROUNDED IN THE INGESTED TITLES, NOT A DICTIONARY. Every term below
-- was taken from the actual text of the 292 Colombian programmes in
-- this database, with its live frequency checked before inclusion —
-- e.g. "aeronáutico" appears in 40 of them, "vehículo" in 19,
-- "blindaje" in 10, "repuestos" in 21.
--
-- FOUR TERMS WERE DELIBERATELY REJECTED after checking what they
-- actually match in the live titles:
--
--   "naval"     — appears 35 times, but almost always as the BUYER's
--                 own base name inside the procedure title ("para la
--                 Base Naval..."), so it would tag a fruit-supply
--                 contract as a naval systems requirement.
--   "cartucho"  — matches printer cartridges ("consumibles para
--                 impresora") as readily as ammunition.
--   "bote"      — matches soft-drink bottles, not boats.
--   "motor"     — too generic in isolation, and the word-boundary
--                 match means it does not help with "automotores"
--                 anyway.
--
-- WEIGHTS mirror the English set: 3 for a term that names the
-- capability itself, 2 for a strong indicator, 1 for a supporting
-- term that should never carry a match on its own.
--
-- ACCENTS: stored naturally accented. app/scoring.py folds both the
-- title and the keyword (NFKD, combining marks stripped) before
-- matching, so "MUNICIÓN" and "municion" both match one stored
-- "munición" — which matters because this feed writes the same word
-- both ways.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/028_spanish_keywords.sql
-- ============================================================

insert into capability_taxonomy_keywords (capability_id, keyword, weight)
select ct.id, k.keyword, k.weight
from (values
    -- Aerospace / aviation — "material aeronáutico" is the single
    -- most common defence phrase in the Colombian feed (40 titles).
    ('AEROSPACE.COMPONENTS', 'material aeronáutico', 3),
    ('AEROSPACE.COMPONENTS', 'aeronáutico', 2),
    ('AEROSPACE.COMPONENTS', 'repuestos aeronáuticos', 3),
    ('AEROSPACE.COMPONENTS', 'aeronave', 2),
    ('AVIATION.MILITARY',    'aeronave', 2),
    ('AVIATION.MILITARY',    'helicóptero', 3),
    ('AVIATION.MILITARY',    'aviación', 2),
    ('AVIATION.MILITARY',    'avión militar', 3),

    -- Land systems
    ('LAND.SYSTEMS', 'vehículo blindado', 3),
    ('LAND.SYSTEMS', 'blindaje', 3),
    ('LAND.SYSTEMS', 'blindado', 3),
    ('LAND.SYSTEMS', 'vehículos militares', 3),
    ('LAND.SYSTEMS', 'automotor', 1),
    ('LAND.SYSTEMS', 'llantas', 1),

    -- Naval — deliberately NOT the bare word "naval" (see header)
    ('NAVAL.SYSTEMS', 'buque', 3),
    ('NAVAL.SYSTEMS', 'navegación marítima', 3),
    ('NAVAL.SYSTEMS', 'embarcación', 2),
    ('NAVAL.SYSTEMS', 'astillero', 2),
    ('NAVAL.SYSTEMS', 'submarino', 3),
    ('NAVAL.SYSTEMS', 'guardacostas', 2),

    -- Weapons, ammunition, manufacturing
    ('MANUFACTURING.DEFENCE', 'munición', 3),
    ('MANUFACTURING.DEFENCE', 'municiones', 3),
    ('MANUFACTURING.DEFENCE', 'armamento', 3),
    ('MANUFACTURING.DEFENCE', 'fusil', 3),
    ('MANUFACTURING.DEFENCE', 'explosivo', 2),
    ('MANUFACTURING.DEFENCE', 'uniformes', 1),
    ('MANUFACTURING.DEFENCE', 'vestuario', 1),
    ('MISSILES.PRECISION',    'misil', 3),
    ('MISSILES.PRECISION',    'misiles', 3),
    ('MISSILES.PRECISION',    'cohete', 2),

    -- Communications
    ('COMMS.SECURE.TACTICAL', 'radiocomunicación', 3),
    ('COMMS.SECURE.TACTICAL', 'comunicaciones tácticas', 3),
    ('COMMS.SECURE.TACTICAL', 'equipos de comunicaciones', 2),
    ('COMMS.SECURE.TACTICAL', 'cifrado', 2),
    ('C4ISR.INTEGRATION',     'mando y control', 3),

    -- Sensing / ISR / optics
    ('SENSING.RADAR',       'radares', 3),
    ('ISR.GENERAL',         'vigilancia', 2),
    ('ISR.GENERAL',         'reconocimiento', 2),
    ('ISR.GENERAL',         'inteligencia militar', 3),
    ('SENSOR.ELECTRO_OPTIC', 'visión nocturna', 3),
    ('SENSOR.ELECTRO_OPTIC', 'optrónica', 3),
    ('SENSOR.ELECTRO_OPTIC', 'cámara térmica', 3),
    ('SENSORS.GENERAL',      'sensores', 2),

    -- Unmanned / autonomy
    ('UAV.INTEGRATION',   'dron', 3),
    ('UAV.INTEGRATION',   'drones', 3),
    ('UAV.INTEGRATION',   'aeronave no tripulada', 3),
    ('AUTONOMY.GENERAL',  'no tripulado', 2),
    ('AUTONOMY.GENERAL',  'autónomo', 2),
    ('AUTONOMY.ROBOTICS', 'robótica', 3),

    -- Counter-UAS and electronic warfare
    ('CUAS.GENERAL', 'antidron', 3),
    ('CUAS.GENERAL', 'contra drones', 3),
    ('EW.GENERAL',   'guerra electrónica', 3),

    -- Space
    ('SPACE.DEFENCE', 'satelital', 3),
    ('SPACE.DEFENCE', 'satélite', 3),

    -- Cyber
    ('CYBER.DEFENCE', 'ciberseguridad', 3),
    ('CYBER.DEFENCE', 'ciberdefensa', 3),

    -- Sonar
    ('SONAR.PASSIVE', 'sonares', 3),
    ('SONAR.PASSIVE', 'hidroacústico', 3),

    -- MRO — both terms are common (mantenimiento 20, repuestos 21)
    -- and generic, so weight 1: they may support a match but must
    -- never carry one on their own.
    ('MRO.GENERAL', 'mantenimiento', 1),
    ('MRO.GENERAL', 'repuestos', 1),
    ('MRO.GENERAL', 'overhaul', 2)
) as k(capability_code, keyword, weight)
join capability_taxonomy ct on ct.code = k.capability_code
where not exists (
    select 1 from capability_taxonomy_keywords existing
    where existing.capability_id = ct.id and existing.keyword = k.keyword
);
