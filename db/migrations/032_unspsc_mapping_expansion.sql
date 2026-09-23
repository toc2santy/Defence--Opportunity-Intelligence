-- ============================================================
-- taxonomy_unspsc_mapping — one verified addition, deliberately not
-- a broad expansion.
--
-- WHY THIS MIGRATION IS SMALL, STATED HONESTLY: the CPV expansion
-- (migration 025) and the NAICS expansion (migration 031) were both
-- verified against reliable, consistent sources — TED prints its own
-- CPV label directly in the notice title, and NAICS codes are
-- confirmed by multiple independent, mutually-agreeing directories
-- (naics.com, Census.gov, IBISWorld). UNSPSC's own 8-digit commodity
-- level has no equally reliable public lookup: a web search attempted
-- during this review for a code appearing 8 times in our own live
-- Colombian data (39121103, on titles literally reading "COMPRA
-- MATERIAL PARA BLINDAJES" / "SERVICIO PRUEBAS BALISTICAS PARA
-- BLINDAJES" — purchase of armor material / ballistic testing
-- service for armor) returned a LOW-CONFIDENCE, internally-uncertain
-- result claiming it was "circuit breakers" — directly contradicting
-- what our own ingested title text plainly says. Rather than trust an
-- unreliable lookup over real evidence, or trust the Spanish title
-- over an unverified code meaning, this migration adds NOTHING for
-- that code. It is left as a genuine, stated follow-up: worth
-- resolving against an authoritative UNSPSC codeset file, not a web
-- search, before mapping it.
--
-- The one addition here WAS reliably confirmed (multiple independent
-- sources agree): UNSPSC family 46100000 = "Light weapons and
-- ammunition". MISSILES.PRECISION already carries the same family
-- (as the shorter "4610" prefix) — reasonable, ammunition is adjacent
-- to precision munitions. But live data shows plain ammunition
-- purchases (e.g. "ADQUISICIÓN DE MUNICIÓN CALIBRE 12.7 X 99 MM")
-- that are general defence manufacturing, not precision-guided
-- systems — so MANUFACTURING.DEFENCE gets the same family too,
-- matching the "several capabilities can honestly share one code"
-- pattern already established for NAICS 334511.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/032_unspsc_mapping_expansion.sql
-- ============================================================

insert into taxonomy_unspsc_mapping (capability_id, unspsc_prefix)
select ct.id, '4610'
from capability_taxonomy ct
where ct.code = 'MANUFACTURING.DEFENCE'
on conflict do nothing;
