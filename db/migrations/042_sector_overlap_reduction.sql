-- ============================================================
-- Reduces two real, verified sources of confusing sector-count
-- overlap on the Industries page — found live (2026-09) after a
-- user reported the per-sector numbers looked "duplicated" and
-- "ambiguous". Not the same bug as migration 041 (NAICS 334511's
-- 11-way over-mapping) — that one is already fixed and correct.
-- This is two NEW, separately-verified findings.
--
-- FINDING 1 — NAICS 336412 ("Aircraft Engine and Engine Parts
-- Manufacturing") was mapped to BOTH AEROSPACE.COMPONENTS and
-- AVIATION.MILITARY (migration 031). Checked the real ingested
-- programme titles carrying this exact code before deciding:
-- "WIRING HARNESS, BRAN_T-56...", "DUCT, FAN, AIRCRAFT G_F100...",
-- "BEARING,ROLLER,CYLI..." — unambiguously component/parts-level
-- items, not aircraft platforms. The NAICS code's own official title
-- says "Engine and Engine Parts" — a components-manufacturing
-- classification, not a platform one (NAICS 336411 "Aircraft
-- Manufacturing" already correctly covers the platform level). This
-- mapping was inflating "Military Aviation" by 760 programmes (47%
-- of that sector's total) with what are really component purchases,
-- creating the exact Aerospace Components <-> Military Aviation
-- overlap a user flagged as confusing. Removed the AVIATION.MILITARY
-- side; AEROSPACE.COMPONENTS (the mapping the code's own title
-- actually supports) is kept.
--
-- FINDING 2 — a genuine miscategorization, not merely an overlap.
-- UNSPSC prefix '2513' (the "Aircraft" family) is mapped to
-- AVIATION.MILITARY + AEROSPACE.COMPONENTS as a broad family-level
-- fallback (migration 015, CanadaBuys). But the single most common
-- 8-digit UNSPSC code actually appearing in real ingested data under
-- that family, 25132102, is not a generic aircraft code at all —
-- verified against GovTribe's own UNSPSC category listing, it is
-- specifically "Military drone". Real programme titles confirm this:
-- "Uncrewed Aircraft System - Light", "Defence Drone Initiative (DDI)
-- Marketplace", "Autonomous Mine Countermeasures (MCM) Uncrewed
-- Surface Vehicle (USV)", "PUMA and RAVEN spare parts" (both real,
-- well-known small UAS platforms) — 412 genuine drone/UAS tenders
-- that were being counted as "Aerospace Components" and "Military
-- Aviation" while NEVER counting toward "UAV / UAS" at all, the
-- literal cause of that sector looking undercounted ("numbers are
-- less") while the other two looked inflated ("duplicated").
--
-- Fixed using the SAME longest-prefix-wins mechanism
-- capability_resolver.py already implements — no code change needed,
-- only data: adding a more specific 8-digit mapping for '25132102'
-- to UAV.INTEGRATION makes it win over the broader 4-digit '2513'
-- family mapping for exactly this code, while every other, genuinely
-- non-UAV code under the '2513' family (e.g. 25131709, confirmed via
-- GovTribe as "Military transport aircraft", 74 real programmes —
-- left untouched, correctly still Aviation) keeps falling back to the
-- family-level mapping as before.
--
-- A dozen much smaller '2513'-family codes (25131600, 25131509,
-- 25131800, etc. — 13, 13, 2... programmes each) were checked and
-- could not be confidently identified from public sources within
-- reasonable effort; left on the broad family-level fallback rather
-- than guessed at, consistent with this project's own standing rule
-- (see migration 041) of narrowing only what can be verified, not
-- everything that theoretically could be narrower.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/042_sector_overlap_reduction.sql
-- ============================================================

delete from taxonomy_naics_mapping
where naics_code = '336412'
  and capability_id in (
    select id from capability_taxonomy where code = 'AVIATION.MILITARY'
  );

insert into taxonomy_unspsc_mapping (capability_id, unspsc_prefix)
select ct.id, '25132102'
from capability_taxonomy ct
where ct.code = 'UAV.INTEGRATION'
on conflict do nothing;
