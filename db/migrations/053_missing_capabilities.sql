-- ============================================================
-- Three missing capabilities (2026-10), found by measuring the stored
-- tenders (11,565) rather than guessing. After migration 052's learner
-- showed that most of the unreachable tenders are civilian, these are the
-- genuinely DEFENCE-relevant buckets that had no capability at all, so a
-- supplier of this kind of product could not even be classified, let alone
-- matched:
--
--   WEAPONS.AMMUNITION   weapons, small arms, ammunition, explosives
--                        (TED 353xx alone: ~190 tenders; keyword count in the
--                        old taxonomy: 1, "guided munition")
--   TRAINING.SIMULATION  simulators, training aids, ranges, targets
--   SPARES.REPLACEMENT   spare and replacement parts (vehicles, ships,
--                        aircraft, equipment) — the bread and butter of a
--                        small supplier; "spare parts" titles were scattered
--                        over dozens of unrelated codes
--
-- DECISIONS WORTH KNOWING:
--  * Mapped codes are only ones actually present in the stored tenders and
--    whose titles I checked. A code pulls EVERY tender carrying it into a
--    product's candidate set, so nothing is mapped "because it sounds right".
--  * Weapons/ammunition CPV codes 353xx were already mapped to
--    MANUFACTURING.DEFENCE / MISSILES.PRECISION. They are ADDED here, not
--    moved (same precedent as CPV 35400000), so tenants who matched on the old
--    capability lose nothing. Cost: those tenders now count in two sectors on
--    the Industries page. UNSPSC is the exception: narrower prefixes
--    ('461015' firearms, '461016' ammunition...) win over the broad '4610'
--    by the resolver's longest-prefix rule, so there they REPLACE the broad
--    attribution rather than duplicating it.
--  * SPARES deliberately does NOT claim the broad AusTender/Canada UNSPSC 25xx
--    codes ("Ship/Military Spare Parts"): a narrower 8-digit mapping would
--    silently pull those tenders out of LAND.SYSTEMS / NAVAL.SYSTEMS in the
--    Customer/OEM views (longest prefix wins). Only unmapped codes and the
--    spares-specific CPV parts codes are used.
--  * Keywords carry singular AND plural forms: scoring.py matches on word
--    boundaries, so "rifle" does not match "rifles" (the migration 034 bug).
--  * "consumables" is left out on purpose: in TED it overwhelmingly means
--    MEDICAL consumables.
--  * Known limit: TRAINING.SIMULATION has few code-mapped tenders (~19), so a
--    simulator product will find little until more simulator codes show up in
--    the data; the learner (migration 052) will propose them as they do.
-- ============================================================

insert into capability_taxonomy (code, label, sector, description)
select v.code, v.label, v.sector, v.description
from (values
    ('WEAPONS.AMMUNITION',
     'Weapons, Ammunition & Explosives Supply Capability',
     'Weapons & Ammunition',
     'Small arms, firearms and their parts, ammunition of all calibres, grenades and explosive ordnance — manufacture or supply.'),
    ('TRAINING.SIMULATION',
     'Training, Simulation & Range Equipment Capability',
     'Training & Simulation',
     'Simulators, synthetic/virtual training systems, training aids, live-fire range equipment and targets for armed forces.'),
    ('SPARES.REPLACEMENT',
     'Spare & Replacement Parts Supply Capability',
     'Spares & Logistics',
     'Spare, repair and replacement parts for military vehicles, ships, aircraft and equipment — supply from stock or build-to-print.')
) as v(code, label, sector, description)
where not exists (select 1 from capability_taxonomy ct where ct.code = v.code);

-- ---------------- classification-code mappings ----------------
insert into taxonomy_cpv_mapping (capability_id, cpv_code)
select ct.id, m.cpv_code
from (values
    ('WEAPONS.AMMUNITION', '35300000'), ('WEAPONS.AMMUNITION', '35310000'), ('WEAPONS.AMMUNITION', '35311000'),
    ('WEAPONS.AMMUNITION', '35320000'), ('WEAPONS.AMMUNITION', '35321000'), ('WEAPONS.AMMUNITION', '35321100'),
    ('WEAPONS.AMMUNITION', '35321200'), ('WEAPONS.AMMUNITION', '35321300'), ('WEAPONS.AMMUNITION', '35322400'),
    ('WEAPONS.AMMUNITION', '35322500'), ('WEAPONS.AMMUNITION', '35330000'), ('WEAPONS.AMMUNITION', '35331000'),
    ('WEAPONS.AMMUNITION', '35331100'), ('WEAPONS.AMMUNITION', '35331300'), ('WEAPONS.AMMUNITION', '35331500'),
    ('WEAPONS.AMMUNITION', '35333100'), ('WEAPONS.AMMUNITION', '35340000'), ('WEAPONS.AMMUNITION', '35341000'),
    ('WEAPONS.AMMUNITION', '35342000'),
    ('TRAINING.SIMULATION', '34152000'), ('TRAINING.SIMULATION', '35740000'), ('TRAINING.SIMULATION', '35210000'),
    ('SPARES.REPLACEMENT', '35420000'), ('SPARES.REPLACEMENT', '35421000'), ('SPARES.REPLACEMENT', '35520000'),
    ('SPARES.REPLACEMENT', '35521100'), ('SPARES.REPLACEMENT', '31680000')
) as m(cap, cpv_code)
join capability_taxonomy ct on ct.code = m.cap
on conflict do nothing;

insert into taxonomy_naics_mapping (capability_id, naics_code)
select ct.id, m.naics_code
from (values
    ('WEAPONS.AMMUNITION', '332992'), ('WEAPONS.AMMUNITION', '332993'),
    ('WEAPONS.AMMUNITION', '332994'), ('WEAPONS.AMMUNITION', '332995')
) as m(cap, naics_code)
join capability_taxonomy ct on ct.code = m.cap
on conflict do nothing;

insert into taxonomy_unspsc_mapping (capability_id, unspsc_prefix)
select ct.id, m.prefix
from (values
    ('WEAPONS.AMMUNITION', '461015'), ('WEAPONS.AMMUNITION', '461016'), ('WEAPONS.AMMUNITION', '461018'),
    ('WEAPONS.AMMUNITION', '4611'),
    ('SPARES.REPLACEMENT', '31160000'), ('SPARES.REPLACEMENT', '31180000'), ('SPARES.REPLACEMENT', '31200000'),
    ('SPARES.REPLACEMENT', '40150000'), ('SPARES.REPLACEMENT', '12350000')
) as m(cap, prefix)
join capability_taxonomy ct on ct.code = m.cap
on conflict do nothing;

-- ---------------- keywords (singular + plural, multi-language) ----------------
insert into capability_taxonomy_keywords (capability_id, keyword, weight)
select ct.id, k.keyword, k.weight
from (values
    -- WEAPONS.AMMUNITION — English
    ('WEAPONS.AMMUNITION', 'ammunition', 3), ('WEAPONS.AMMUNITION', 'munition', 3), ('WEAPONS.AMMUNITION', 'munitions', 3),
    ('WEAPONS.AMMUNITION', 'small arms', 3), ('WEAPONS.AMMUNITION', 'firearm', 3), ('WEAPONS.AMMUNITION', 'firearms', 3),
    ('WEAPONS.AMMUNITION', 'rifle', 2), ('WEAPONS.AMMUNITION', 'rifles', 2), ('WEAPONS.AMMUNITION', 'pistol', 2),
    ('WEAPONS.AMMUNITION', 'pistols', 2), ('WEAPONS.AMMUNITION', 'machine gun', 3), ('WEAPONS.AMMUNITION', 'machine guns', 3),
    ('WEAPONS.AMMUNITION', 'grenade', 3), ('WEAPONS.AMMUNITION', 'grenades', 3), ('WEAPONS.AMMUNITION', 'explosive ordnance', 3),
    ('WEAPONS.AMMUNITION', 'explosives', 2), ('WEAPONS.AMMUNITION', 'detonator', 2), ('WEAPONS.AMMUNITION', 'detonators', 2),
    ('WEAPONS.AMMUNITION', 'ordnance', 2), ('WEAPONS.AMMUNITION', 'weapons', 2), ('WEAPONS.AMMUNITION', 'weapon accessories', 2),
    ('WEAPONS.AMMUNITION', 'weapon components', 2), ('WEAPONS.AMMUNITION', 'cartridge', 1), ('WEAPONS.AMMUNITION', 'cartridges', 1),
    ('WEAPONS.AMMUNITION', '155mm', 3),
    -- Spanish / French / German / Czech / Polish / Ukrainian
    ('WEAPONS.AMMUNITION', 'munición', 3), ('WEAPONS.AMMUNITION', 'municiones', 3), ('WEAPONS.AMMUNITION', 'armamento', 2),
    ('WEAPONS.AMMUNITION', 'fusil', 2), ('WEAPONS.AMMUNITION', 'fusiles', 2), ('WEAPONS.AMMUNITION', 'pistolas', 2),
    ('WEAPONS.AMMUNITION', 'granadas', 3), ('WEAPONS.AMMUNITION', 'armes à feu', 3), ('WEAPONS.AMMUNITION', 'fusils', 2),
    ('WEAPONS.AMMUNITION', 'munitionen', 3), ('WEAPONS.AMMUNITION', 'feuerwaffen', 3), ('WEAPONS.AMMUNITION', 'schusswaffen', 3),
    ('WEAPONS.AMMUNITION', 'patronen', 2), ('WEAPONS.AMMUNITION', 'střelivo', 3), ('WEAPONS.AMMUNITION', 'náboje', 2),
    ('WEAPONS.AMMUNITION', 'amunicja', 3), ('WEAPONS.AMMUNITION', 'amunicji', 3), ('WEAPONS.AMMUNITION', 'broń palna', 3),
    ('WEAPONS.AMMUNITION', 'боєприпаси', 3), ('WEAPONS.AMMUNITION', 'набої', 3), ('WEAPONS.AMMUNITION', 'зброя', 2),
    ('WEAPONS.AMMUNITION', 'гранати', 3), ('WEAPONS.AMMUNITION', 'стрілецька зброя', 3),

    -- TRAINING.SIMULATION
    ('TRAINING.SIMULATION', 'simulator', 3), ('TRAINING.SIMULATION', 'simulators', 3), ('TRAINING.SIMULATION', 'simulation', 2),
    ('TRAINING.SIMULATION', 'flight simulator', 3), ('TRAINING.SIMULATION', 'training simulator', 3),
    ('TRAINING.SIMULATION', 'combat simulator', 3), ('TRAINING.SIMULATION', 'training aid', 2), ('TRAINING.SIMULATION', 'training aids', 2),
    ('TRAINING.SIMULATION', 'training system', 2), ('TRAINING.SIMULATION', 'training systems', 2),
    ('TRAINING.SIMULATION', 'firing range', 3), ('TRAINING.SIMULATION', 'shooting range', 3), ('TRAINING.SIMULATION', 'live fire', 2),
    ('TRAINING.SIMULATION', 'target systems', 2), ('TRAINING.SIMULATION', 'wargaming', 3), ('TRAINING.SIMULATION', 'synthetic training', 3),
    ('TRAINING.SIMULATION', 'tactical trainer', 3), ('TRAINING.SIMULATION', 'mission trainer', 3), ('TRAINING.SIMULATION', 'virtual training', 2),
    ('TRAINING.SIMULATION', 'military training', 2), ('TRAINING.SIMULATION', 'marksmanship', 3),
    ('TRAINING.SIMULATION', 'simulador', 3), ('TRAINING.SIMULATION', 'simuladores', 3), ('TRAINING.SIMULATION', 'entrenamiento militar', 2),
    ('TRAINING.SIMULATION', 'simulateur', 3), ('TRAINING.SIMULATION', 'simulateurs', 3),
    ('TRAINING.SIMULATION', 'symulator', 3), ('TRAINING.SIMULATION', 'symulatory', 3), ('TRAINING.SIMULATION', 'symulatora', 3),
    ('TRAINING.SIMULATION', 'sprzętu symulacyjnego', 3),
    ('TRAINING.SIMULATION', 'симулятор', 3), ('TRAINING.SIMULATION', 'симулятори', 3), ('TRAINING.SIMULATION', 'тренувальні', 2),
    ('TRAINING.SIMULATION', 'мішені', 2), ('TRAINING.SIMULATION', 'мішеневі', 2),

    -- SPARES.REPLACEMENT
    ('SPARES.REPLACEMENT', 'spare part', 3), ('SPARES.REPLACEMENT', 'spare parts', 3), ('SPARES.REPLACEMENT', 'spares', 2),
    ('SPARES.REPLACEMENT', 'replacement part', 3), ('SPARES.REPLACEMENT', 'replacement parts', 3),
    ('SPARES.REPLACEMENT', 'repair parts', 2), ('SPARES.REPLACEMENT', 'repuesto', 3), ('SPARES.REPLACEMENT', 'repuestos', 3),
    ('SPARES.REPLACEMENT', 'náhradní díly', 3), ('SPARES.REPLACEMENT', 'náhradných dielov', 3), ('SPARES.REPLACEMENT', 'náhradné diely', 3),
    ('SPARES.REPLACEMENT', 'ersatzteil', 3), ('SPARES.REPLACEMENT', 'ersatzteile', 3), ('SPARES.REPLACEMENT', 'pièces de rechange', 3),
    ('SPARES.REPLACEMENT', 'pièce de rechange', 3), ('SPARES.REPLACEMENT', 'części zamienne', 3),
    ('SPARES.REPLACEMENT', 'запчастини', 3), ('SPARES.REPLACEMENT', 'запасні частини', 3)
) as k(cap, keyword, weight)
join capability_taxonomy ct on ct.code = k.cap
where not exists (
    select 1 from capability_taxonomy_keywords e
    where e.capability_id = ct.id and e.keyword = k.keyword
);
