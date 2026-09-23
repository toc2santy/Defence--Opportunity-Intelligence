-- ============================================================
-- Seeds the rotation-state row for CPPP's new page-offset rotation
-- (see app/cppp_india_ingestion.py's CPPP_ROTATION_KEY comment for
-- the real problem this fixes: every run always started at page 1,
-- so repeated runs mostly re-scanned the same top-40 pages instead of
-- reaching further into the ~32,000-tender listing — measured live,
-- a full 40-page run after the reconnect fix added only 3 genuinely
-- new programmes on top of 61 already stored).
--
-- Same table, same pattern as migration 008's SAM.gov NAICS rotation
-- — reusing app.ingestion_common.get_rotation_index/
-- advance_rotation_index rather than inventing a second mechanism.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/037_cppp_page_rotation.sql
-- ============================================================

insert into ingestion_rotation_state (source_name, rotation_index)
select 'cppp_india_page_offset', 0
where not exists (
    select 1 from ingestion_rotation_state where source_name = 'cppp_india_page_offset'
);
