-- ============================================================
-- taxonomy_naics_mapping supplement — the genuinely NEW codes, on
-- top of the ingestion-rotation fix in app/sam_gov_normalize.py
-- (DEFENSE_RELEVANT_NAICS 6 -> 14 codes).
--
-- MOST of the coverage gap a Report Intel review found turned out
-- NOT to be a mapping problem: NAVAL.SYSTEMS (336611), COMMS.SECURE.
-- TACTICAL (334220), AEROSPACE.COMPONENTS (336413) and SPACE.DEFENCE
-- (517410) already had correct codes mapped — they could simply
-- never appear, because SAM.gov was never QUERIED for those codes.
-- That is the bigger, separate fix (see sam_gov_normalize.py).
--
-- This migration is only the codes that were genuinely missing:
--
--   SONAR.PASSIVE had ZERO NAICS coverage at all. NAICS has no
--   6-digit code specific to sonar (confirmed: the finest applicable
--   code is 334511 "Search, Detection, Navigation, Guidance,
--   Aeronautical, and Nautical System and Instrument Manufacturing"
--   — the SAME code already shared by nine other capabilities here).
--   This is an honest NAICS granularity limit, not a mapping mistake:
--   NAICS classifies by INDUSTRY, and a sonar-systems manufacturer
--   and a radar-systems manufacturer are the same industry code.
--   Given the existing keyword_score==0-always-low guard already caps
--   a code-only match regardless of how many capabilities share the
--   code, adding it here is safe — it makes SONAR.PASSIVE reachable
--   at all, at the same (honest) precision every other 334511 user
--   already accepts.
--
--   CYBER.DEFENCE and C4ISR.INTEGRATION were both leaning entirely on
--   541712 ("R&D in the Physical, Engineering, and Life Sciences") —
--   a genuinely generic catch-all. 541512 "Computer Systems Design
--   Services" is a real, more specific NAICS code for exactly this
--   work (verified: naics.com, Census.gov, IBISWorld, HigherGov all
--   agree on the title) and is added alongside the existing code, not
--   instead of it.
--
--   MRO.GENERAL had only 488190 ("Other Support Activities for Air
--   Transportation" — confirmed via Census.gov's own description
--   this DOES cover aircraft maintenance/repair specifically). Added
--   811210 "Electronic and Precision Equipment Repair and
--   Maintenance" alongside it, since MRO in this platform's own
--   engine description is not aircraft-only — 811210 covers repair of
--   the sensors/avionics/radar equipment that has no MRO code of its
--   own otherwise.
--
--   AEROSPACE.COMPONENTS and AVIATION.MILITARY both had airframe/
--   parts codes but nothing engine-specific. Added 336412 "Aircraft
--   Engine and Engine Parts Manufacturing" (verified, distinct 2022
--   NAICS code) to both.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/031_naics_mapping_expansion.sql
-- ============================================================

insert into taxonomy_naics_mapping (capability_id, naics_code)
select ct.id, m.naics_code
from (values
    ('SONAR.PASSIVE',        '334511'),
    ('CYBER.DEFENCE',        '541512'),
    ('C4ISR.INTEGRATION',    '541512'),
    ('MRO.GENERAL',          '811210'),
    ('AEROSPACE.COMPONENTS', '336412'),
    ('AVIATION.MILITARY',    '336412')
) as m(capability_code, naics_code)
join capability_taxonomy ct on ct.code = m.capability_code
on conflict do nothing;
