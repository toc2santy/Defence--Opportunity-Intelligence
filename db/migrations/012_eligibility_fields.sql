-- ============================================================
-- Two real fixes, found together while building Stage 1 of the
-- eligibility feature:
--
-- 1. A REGRESSION FIX: naics_code has never actually been stored
--    by the ongoing ingestion INSERT since migration 004 backfilled
--    it once for historical data. Every real SAM.gov record ingested
--    since then has silently gotten naics_code = NULL, breaking
--    Phase 3 matching for anything newly ingested. This migration
--    doesn't fix the INSERT itself (that's a code change, see
--    app/sam_gov_ingestion.py) — it re-runs the same backfill logic
--    from migration 004 to catch anything that slipped through
--    since then.
--
-- 2. THE ACTUAL FEATURE: surfaces SAM.gov's own set-aside/eligibility
--    fields (typeOfSetAside / typeOfSetAsideDescription) — real,
--    already-fetched data that was being discarded before storage —
--    plus response_deadline, flagged as a gap in the Engine 8
--    ("Procurement Intelligence") honesty audit.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/012_eligibility_fields.sql
-- ============================================================

alter table programmes add column if not exists set_aside_code text;
alter table programmes add column if not exists set_aside_description text;
-- Stored as text, not timestamptz — SAM.gov's exact date-string
-- format for this field isn't confirmed with full certainty, and a
-- cast failure on one malformed value should never be able to fail
-- an entire ingestion run. Same defensive philosophy as everywhere
-- else uncertain external data is handled in this codebase.
alter table programmes add column if not exists response_deadline text;

-- Re-run the same one-time backfill migration 004 did (same regex
-- pattern, for exact consistency), in case any naics_code went
-- missing for programmes ingested since then.
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
