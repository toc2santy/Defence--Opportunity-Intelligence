-- ============================================================
-- New capability: TACTICAL.PROTECTIVE_EQUIPMENT.
--
-- WHY THIS ONE, out of everything unmapped: a Report Intel review
-- found the single largest real gap in the 21-capability taxonomy by
-- querying our own already-ingested data for classification codes
-- that appear frequently but match NO current capability. The
-- clearest, most coherent cluster by far was personal protective and
-- tactical equipment — bullet-proof vests, combat uniforms, military
-- helmets — which had no home at all despite real live volume:
--
--   CPV 35815100  Bullet-proof vests             — 26 programmes
--   CPV 35813000  Military helmets               — 18 programmes
--   CPV 35812000  Combat uniforms                — 17 programmes
--   CPV 35811300  Military uniforms              — 25 programmes
--   CPV 35113400  Protective and safety clothing — 82 programmes
--
-- (counts from live EU TED data, 2026-09-16; CPV labels are TED's OWN
-- printed labels on those notices — see migration 025's header for
-- the same verification method.)
--
-- NAICS 339113 "Surgical Appliance and Supplies Manufacturing" is
-- confirmed (naics.com / Ask Kodiak's own NAICS classification guide)
-- to include body armor and personal protective equipment
-- manufacturing specifically — not an obvious code from its title
-- alone, which is why it needed checking rather than assuming.
--
-- WHAT IS DELIBERATELY *NOT* INCLUDED, a real finding worth flagging
-- rather than quietly acting on: the SAME CPV neighbourhood also
-- contains a much larger volume of police and fire-brigade equipment
-- (35811100 Fire-brigade uniforms, 35811200 Police uniforms — 26 and
-- 60 live programmes respectively; 34144210/34144213 Fire engines —
-- 97+27) currently being ingested under UK Find a Tender and TED's
-- "entire CPV division 35 is defence/security-relevant" rule
-- (app/uk_ft_normalize.is_defense_relevant_cpv). Whether that
-- division-wide rule should be narrowed to exclude pure civilian
-- police/fire-brigade notices is a real, separate, platform-wide
-- ingestion-relevance question with its own tradeoffs (it would also
-- exclude genuinely defence-adjacent CBRN/crash-rescue equipment) —
-- deliberately left as an open question for a human decision, not
-- resolved here as a side effect of adding one capability.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/033_tactical_protective_equipment.sql
-- ============================================================

insert into capability_taxonomy (code, label, sector, description)
select 'TACTICAL.PROTECTIVE_EQUIPMENT',
       'Personal Protective & Tactical Equipment Subsystem',
       'Personal Equipment',
       'Body armour, ballistic protection, combat uniforms, military helmets and related individual protective equipment — deliberately scoped to military-specific items, not general police/fire-brigade uniform or vehicle procurement.'
where not exists (
    select 1 from capability_taxonomy where code = 'TACTICAL.PROTECTIVE_EQUIPMENT'
);

insert into taxonomy_cpv_mapping (capability_id, cpv_code)
select ct.id, m.cpv_code
from (values
    ('35815100'), ('35813000'), ('35812000'), ('35811300'), ('35113400')
) as m(cpv_code)
cross join lateral (select id from capability_taxonomy where code = 'TACTICAL.PROTECTIVE_EQUIPMENT') ct
on conflict do nothing;

insert into taxonomy_naics_mapping (capability_id, naics_code)
select ct.id, '339113'
from capability_taxonomy ct
where ct.code = 'TACTICAL.PROTECTIVE_EQUIPMENT'
on conflict do nothing;

-- Keywords — English and Spanish both, grounded the same way the
-- Colombia expansion was: real ingested title text
-- ("ADQUISICIÓN DE UNIFORMES DE FAENA", "chaleco antibalas" is the
-- standard Spanish term for a ballistic vest even though it did not
-- happen to appear in the sampled titles directly).
insert into capability_taxonomy_keywords (capability_id, keyword, weight)
select ct.id, k.keyword, k.weight
from (values
    ('body armor', 3), ('body armour', 3), ('bullet-proof vest', 3), ('bulletproof vest', 3),
    ('ballistic vest', 3), ('ballistic protection', 2), ('combat uniform', 3),
    ('military uniform', 2), ('military helmet', 3), ('tactical vest', 2),
    ('protective clothing', 1), ('personal protective equipment', 1),
    ('chaleco antibalas', 3), ('chaleco balístico', 3), ('uniforme de combate', 3),
    ('casco militar', 3), ('blindaje personal', 2), ('protección balística', 2)
) as k(keyword, weight)
cross join lateral (select id from capability_taxonomy where code = 'TACTICAL.PROTECTIVE_EQUIPMENT') ct
where not exists (
    select 1 from capability_taxonomy_keywords existing
    where existing.capability_id = ct.id and existing.keyword = k.keyword
);
