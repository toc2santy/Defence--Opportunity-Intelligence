-- ============================================================
-- Phase 3 migration: real Programme Intelligence matching.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/004_programme_matching.sql
-- ============================================================

-- naics_code was only ever stored inside evidence.claim as free
-- text (e.g. "...NAICS 334511..."), which meant it could never be
-- filtered on efficiently. Promote it to a real column. Existing
-- rows get backfilled by parsing the claim text they already have
-- — a one-time migration step, not something matching will rely
-- on going forward (ingestion now writes this column directly).
alter table programmes add column if not exists naics_code text;
create index if not exists idx_programmes_naics on programmes(naics_code);

update programmes p
set naics_code = sub.code
from (
    select e.related_entity_id as programme_id,
           substring(e.claim from 'NAICS ([A-Za-z0-9]+)') as code
    from evidence e
    where e.related_entity_type = 'programme'
) sub
where p.id = sub.programme_id
  and p.naics_code is null
  and sub.code is not null;

-- ------------------------------------------------------------
-- Curated mapping: which NAICS codes are relevant to which
-- capability taxonomy node. This is real domain judgment, not
-- derivable automatically — treat this as a v1 starting set that
-- needs ongoing refinement by someone who actually knows defense
-- procurement categorization, same honesty as the Phase 1 keyword
-- seed data.
-- ------------------------------------------------------------
create table taxonomy_naics_mapping (
    id              uuid primary key default gen_random_uuid(),
    capability_id   uuid not null references capability_taxonomy(id) on delete cascade,
    naics_code      text not null,
    unique (capability_id, naics_code)
);

insert into taxonomy_naics_mapping (capability_id, naics_code)
select ct.id, m.naics_code
from capability_taxonomy ct
join (values
    ('UAV.INTEGRATION', '336411'),
    ('UAV.INTEGRATION', '334511'),
    ('SENSING.RADAR', '334511'),
    ('EW.GENERAL', '334511'),
    ('EW.GENERAL', '541712'),
    ('CYBER.DEFENCE', '541712'),
    ('CYBER.DEFENCE', '928110'),
    ('C4ISR.INTEGRATION', '334511'),
    ('C4ISR.INTEGRATION', '541712'),
    ('NAVAL.SYSTEMS', '336611'),
    ('SENSOR.ELECTRO_OPTIC', '334511'),
    ('AUTONOMY.GENERAL', '541712'),
    ('AUTONOMY.GENERAL', '334511'),
    ('COMMS.SECURE.TACTICAL', '334220'),
    ('LAND.SYSTEMS', '336992'),
    ('AVIATION.MILITARY', '336411'),
    ('CUAS.GENERAL', '334511'),
    ('ISR.GENERAL', '334511'),
    ('ISR.GENERAL', '541712'),
    ('AUTONOMY.ROBOTICS', '334511'),
    ('AUTONOMY.ROBOTICS', '541712'),
    ('SPACE.DEFENCE', '336414'),
    ('SPACE.DEFENCE', '517410'),
    ('MISSILES.PRECISION', '336414'),
    ('SENSORS.GENERAL', '334511'),
    ('AEROSPACE.COMPONENTS', '336413'),
    ('MRO.GENERAL', '488190'),
    ('MANUFACTURING.DEFENCE', '336992')
) as m(code, naics_code) on m.code = ct.code
on conflict (capability_id, naics_code) do nothing;

-- Without this, re-running matching for the same product would
-- create duplicate opportunity rows for the same programme every
-- time instead of updating the existing one.
alter table opportunities
    add constraint uq_opportunities_product_programme unique (product_id, programme_id);
