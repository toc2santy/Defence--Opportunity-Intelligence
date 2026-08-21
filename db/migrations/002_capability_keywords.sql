-- ============================================================
-- Phase 1 migration: real rules-based Capability Intelligence.
--
-- Run this manually against the running database (Alembic
-- migration history is still a noted follow-up from Phase 0 —
-- this is a numbered hand-applied migration in the meantime,
-- same pattern used for the audit_log RLS fix earlier).
--
--   docker compose exec db psql -U postgres -d doi -f /dev/stdin < db/migrations/002_capability_keywords.sql
--
-- (or paste the contents into psql directly)
-- ============================================================

-- ------------------------------------------------------------
-- Keyword table — replaces the prototype's 11-line hardcoded
-- lookup with real, extensible, weighted data. Multiple keywords
-- per taxonomy node, each with a weight reflecting how specific
-- a signal that term is (3 = strong/specific, 1 = generic).
-- ------------------------------------------------------------
create table capability_taxonomy_keywords (
    id              uuid primary key default gen_random_uuid(),
    capability_id   uuid not null references capability_taxonomy(id) on delete cascade,
    keyword         text not null,
    weight          smallint not null default 1 check (weight between 1 and 5)
);
create index idx_taxonomy_keywords_capability on capability_taxonomy_keywords(capability_id);

-- product_capabilities was missing RLS from Phase 0 — a real gap,
-- same class of issue as the audit_log one found earlier. It has
-- no tenant_id column directly, so scope via the owning product.
alter table product_capabilities enable row level security;
create policy tenant_isolation_product_capabilities on product_capabilities
    using (
        product_id in (
            select id from products
            where tenant_id = current_setting('app.current_tenant', true)::uuid
        )
    );

-- ------------------------------------------------------------
-- A source row representing the classifier itself, so every
-- classification's evidence traces to something real instead of
-- a null source. Idempotent without a unique constraint.
-- ------------------------------------------------------------
insert into sources (name, source_type, trust_level, terms_notes)
select 'Internal Rules-Based Capability Classifier', 'internal_analyst', 'medium',
       'Deterministic weighted-keyword matching engine, Phase 1. Not machine learning — every match is explainable from the keyword table.'
where not exists (
    select 1 from sources where name = 'Internal Rules-Based Capability Classifier'
);

-- ------------------------------------------------------------
-- Expand taxonomy to cover the remaining sectors named in the
-- original product brief that Phase 0's 9-entry seed didn't reach.
-- ------------------------------------------------------------
insert into capability_taxonomy (code, label, sector) values
    ('LAND.SYSTEMS', 'Land Systems Platform Subsystem', 'Land Systems'),
    ('AVIATION.MILITARY', 'Military Aviation Subsystem', 'Military Aviation'),
    ('CUAS.GENERAL', 'Counter-UAS Subsystem', 'C-UAS'),
    ('ISR.GENERAL', 'Intelligence, Surveillance & Reconnaissance Capability', 'ISR'),
    ('AUTONOMY.ROBOTICS', 'Robotics Platform Capability', 'Robotics'),
    ('SPACE.DEFENCE', 'Space & Defence Space Capability', 'Space & Defence Space'),
    ('MISSILES.PRECISION', 'Missiles & Precision Systems Subsystem', 'Missiles & Precision Systems'),
    ('SENSORS.GENERAL', 'General Sensor Subsystem', 'Sensors'),
    ('AEROSPACE.COMPONENTS', 'Aerospace Component Manufacturing Capability', 'Aerospace Components'),
    ('MRO.GENERAL', 'Maintenance, Repair & Overhaul Capability', 'MRO'),
    ('MANUFACTURING.DEFENCE', 'Defence Manufacturing Capability', 'Defence Manufacturing')
on conflict (code) do nothing;

-- ------------------------------------------------------------
-- Keyword seed data. This is a starting set grounded in real
-- domain terminology, not exhaustive — extending this list with
-- actual analyst/domain-expert input is real, ongoing work, not
-- a one-time task. Treat this as v1, not a finished taxonomy.
-- ------------------------------------------------------------
insert into capability_taxonomy_keywords (capability_id, keyword, weight)
select ct.id, k.keyword, k.weight
from capability_taxonomy ct
join (values
    ('COMMS.SECURE.TACTICAL', 'secure communication', 3),
    ('COMMS.SECURE.TACTICAL', 'encrypted', 3),
    ('COMMS.SECURE.TACTICAL', 'tactical radio', 3),
    ('COMMS.SECURE.TACTICAL', 'secure comms', 3),
    ('COMMS.SECURE.TACTICAL', 'encryption', 2),
    ('COMMS.SECURE.TACTICAL', 'crypto', 2),
    ('COMMS.SECURE.TACTICAL', 'anti-jam', 2),
    ('COMMS.SECURE.TACTICAL', 'frequency hopping', 2),
    ('COMMS.SECURE.TACTICAL', 'communication', 1),

    ('UAV.INTEGRATION', 'uav', 3),
    ('UAV.INTEGRATION', 'drone', 3),
    ('UAV.INTEGRATION', 'unmanned aerial', 3),
    ('UAV.INTEGRATION', 'uas', 3),
    ('UAV.INTEGRATION', 'unmanned aircraft', 3),
    ('UAV.INTEGRATION', 'quadcopter', 2),
    ('UAV.INTEGRATION', 'vtol', 2),

    ('SENSING.RADAR', 'radar', 3),
    ('SENSING.RADAR', 'synthetic aperture radar', 3),
    ('SENSING.RADAR', 'sar', 2),
    ('SENSING.RADAR', 'doppler', 2),
    ('SENSING.RADAR', 'signal processing', 1),
    ('SENSING.RADAR', 'detection range', 1),

    ('EW.GENERAL', 'electronic warfare', 3),
    ('EW.GENERAL', 'jamming', 3),
    ('EW.GENERAL', 'jammer', 3),
    ('EW.GENERAL', 'ew suite', 3),
    ('EW.GENERAL', 'signal intelligence', 2),
    ('EW.GENERAL', 'sigint', 2),
    ('EW.GENERAL', 'spectrum', 1),
    ('EW.GENERAL', 'countermeasure', 2),

    ('CYBER.DEFENCE', 'cyber', 3),
    ('CYBER.DEFENCE', 'cybersecurity', 3),
    ('CYBER.DEFENCE', 'intrusion detection', 2),
    ('CYBER.DEFENCE', 'network defence', 2),
    ('CYBER.DEFENCE', 'vulnerability', 1),
    ('CYBER.DEFENCE', 'penetration testing', 2),

    ('C4ISR.INTEGRATION', 'c4isr', 3),
    ('C4ISR.INTEGRATION', 'command and control', 3),
    ('C4ISR.INTEGRATION', 'c2', 2),
    ('C4ISR.INTEGRATION', 'battle management', 2),
    ('C4ISR.INTEGRATION', 'situational awareness', 2),

    ('NAVAL.SYSTEMS', 'naval', 3),
    ('NAVAL.SYSTEMS', 'ship', 2),
    ('NAVAL.SYSTEMS', 'submarine', 3),
    ('NAVAL.SYSTEMS', 'sonar', 3),
    ('NAVAL.SYSTEMS', 'maritime', 2),
    ('NAVAL.SYSTEMS', 'vessel', 2),

    ('SENSOR.ELECTRO_OPTIC', 'electro-optic', 3),
    ('SENSOR.ELECTRO_OPTIC', 'eo/ir', 3),
    ('SENSOR.ELECTRO_OPTIC', 'infrared', 2),
    ('SENSOR.ELECTRO_OPTIC', 'thermal imaging', 2),
    ('SENSOR.ELECTRO_OPTIC', 'targeting pod', 3),
    ('SENSOR.ELECTRO_OPTIC', 'camera gimbal', 2),

    ('AUTONOMY.GENERAL', 'autonomous', 3),
    ('AUTONOMY.GENERAL', 'ai-enabled', 2),
    ('AUTONOMY.GENERAL', 'machine learning', 2),
    ('AUTONOMY.GENERAL', 'artificial intelligence', 2),
    ('AUTONOMY.GENERAL', 'autonomy', 3),

    ('LAND.SYSTEMS', 'armoured vehicle', 3),
    ('LAND.SYSTEMS', 'tank', 3),
    ('LAND.SYSTEMS', 'ground vehicle', 2),
    ('LAND.SYSTEMS', 'artillery', 3),
    ('LAND.SYSTEMS', 'land platform', 2),

    ('AVIATION.MILITARY', 'fighter jet', 3),
    ('AVIATION.MILITARY', 'military aircraft', 3),
    ('AVIATION.MILITARY', 'avionics', 2),
    ('AVIATION.MILITARY', 'cockpit', 2),
    ('AVIATION.MILITARY', 'flight control', 2),

    ('CUAS.GENERAL', 'counter-uas', 3),
    ('CUAS.GENERAL', 'counter-drone', 3),
    ('CUAS.GENERAL', 'anti-drone', 3),
    ('CUAS.GENERAL', 'drone defence', 2),

    ('ISR.GENERAL', 'isr', 3),
    ('ISR.GENERAL', 'reconnaissance', 3),
    ('ISR.GENERAL', 'surveillance', 3),
    ('ISR.GENERAL', 'intelligence gathering', 2),

    ('AUTONOMY.ROBOTICS', 'robotics', 3),
    ('AUTONOMY.ROBOTICS', 'robotic', 2),
    ('AUTONOMY.ROBOTICS', 'ground robot', 2),
    ('AUTONOMY.ROBOTICS', 'unmanned ground vehicle', 3),
    ('AUTONOMY.ROBOTICS', 'ugv', 2),

    ('SPACE.DEFENCE', 'satellite', 3),
    ('SPACE.DEFENCE', 'space-based', 3),
    ('SPACE.DEFENCE', 'orbital', 2),
    ('SPACE.DEFENCE', 'launch vehicle', 2),

    ('MISSILES.PRECISION', 'missile', 3),
    ('MISSILES.PRECISION', 'precision-guided', 3),
    ('MISSILES.PRECISION', 'guided munition', 3),
    ('MISSILES.PRECISION', 'warhead', 2),

    ('SENSORS.GENERAL', 'sensor', 2),
    ('SENSORS.GENERAL', 'sensing', 1),
    ('SENSORS.GENERAL', 'sensor fusion', 2),
    ('SENSORS.GENERAL', 'lidar', 2),

    ('AEROSPACE.COMPONENTS', 'aerospace component', 3),
    ('AEROSPACE.COMPONENTS', 'airframe', 2),
    ('AEROSPACE.COMPONENTS', 'landing gear', 2),
    ('AEROSPACE.COMPONENTS', 'turbine blade', 2),

    ('MRO.GENERAL', 'maintenance', 2),
    ('MRO.GENERAL', 'repair and overhaul', 3),
    ('MRO.GENERAL', 'mro', 3),
    ('MRO.GENERAL', 'depot maintenance', 2),

    ('MANUFACTURING.DEFENCE', 'defence manufacturing', 3),
    ('MANUFACTURING.DEFENCE', 'precision machining', 2),
    ('MANUFACTURING.DEFENCE', 'fabrication', 1)
) as k(code, keyword, weight) on k.code = ct.code;
