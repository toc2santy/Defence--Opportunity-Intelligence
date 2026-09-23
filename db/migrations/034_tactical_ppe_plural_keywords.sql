-- ============================================================
-- Plural keyword forms for TACTICAL.PROTECTIVE_EQUIPMENT
-- (migration 033) — a real bug found immediately by live-testing the
-- new capability, not a hypothetical.
--
-- migration 033's English keywords were all singular ("bullet-proof
-- vest", "military helmet", "military uniform", "combat uniform").
-- The word-boundary regex in app/scoring.py (`\bKEYWORD\b`) requires
-- a boundary immediately AFTER the keyword too — so "military
-- helmet" does not match inside "Military helmets", because there is
-- no boundary between "t" and the following "s" (both word
-- characters). Verified against this platform's OWN ingested TED
-- data, which prints these five CPV labels PLURAL, every time,
-- 100% of the sampled titles (80/80, 25/25, 17/17, 18/18, 26/26):
--   "Protective and safety clothing" (already an uncountable noun,
--   unaffected), "Military uniforms", "Combat uniforms", "Military
--   helmets", "Bullet-proof vests".
--
-- End-to-end proof this mattered: before this migration, a test
-- product confirmed for this capability produced 168 matches with
-- ZERO at high confidence and only 2 at medium — every single
-- code-matched title like "Poland – Military helmets – ..." scored
-- keyword_score = 0 and was capped at "low" purely because of this
-- plural mismatch, not because the match was actually weak.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/034_tactical_ppe_plural_keywords.sql
-- ============================================================

insert into capability_taxonomy_keywords (capability_id, keyword, weight)
select ct.id, k.keyword, k.weight
from (values
    ('bullet-proof vests', 3), ('bulletproof vests', 3), ('ballistic vests', 3),
    ('combat uniforms', 3), ('military uniforms', 2), ('military helmets', 3),
    ('tactical vests', 2), ('body armors', 3)
) as k(keyword, weight)
cross join lateral (select id from capability_taxonomy where code = 'TACTICAL.PROTECTIVE_EQUIPMENT') ct
where not exists (
    select 1 from capability_taxonomy_keywords existing
    where existing.capability_id = ct.id and existing.keyword = k.keyword
);
