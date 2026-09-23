-- ============================================================
-- How many times the user has opened a given tender on its source
-- portal, and when they last did.
--
-- WHY A COUNTER COLUMN AND NOT JUST MORE audit_log ROWS:
-- The first open is a genuine engagement event and belongs in the
-- Engagement History timeline. The fifth open of the same tender is
-- not a new event — it's the same one happening again, and writing a
-- row for each would bury real pipeline activity (stage changes)
-- under repeated "opened tender" noise in the one timeline a user
-- reads to understand what happened on an opportunity.
--
-- So: audit_log gets exactly one row, on the FIRST open; this counter
-- carries the repeat count. The counter deliberately does not live in
-- audit_log itself — an audit trail's rows should never be rewritten
-- after the fact, which is the intent db/schema.sql states for that
-- table (note: the live grants are currently wider than that comment
-- claims — doi_app does hold UPDATE/DELETE on audit_log — so the
-- append-only property rests on nothing in the code doing it).
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/024_tender_opened_counter.sql
-- ============================================================

alter table opportunities add column if not exists tender_opened_count integer not null default 0;
alter table opportunities add column if not exists tender_last_opened_at timestamptz;

-- Backfill from the events already recorded before this counter
-- existed, so nobody's history silently resets to zero: one open per
-- opportunity that has a tender_opened entry, timestamped from it.
update opportunities o
set tender_opened_count = 1,
    tender_last_opened_at = a.last_opened
from (
    select entity_id::uuid as opportunity_id, max(created_at) as last_opened
    from audit_log
    where entity_type = 'opportunity' and action = 'opportunity.tender_opened'
    group by entity_id
) a
where a.opportunity_id = o.id and o.tender_opened_count = 0;
