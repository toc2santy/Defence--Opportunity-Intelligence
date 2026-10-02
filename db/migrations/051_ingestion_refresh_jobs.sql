-- ============================================================
-- Periodic refresh of already-ingested SAM.gov notices (2026-09) —
-- a real, structural gap found live: ingestion is insert-only, so a
-- notice amended AFTER it was first stored (a real, common case —
-- SAM.gov's own "additionalInfo.sections" flags a "contact" section
-- as "updated" independently of the notice's initial post) never
-- gets that update picked up. Confirmed live on a real user-reported
-- tender ("Skydio X10 Drone... for WAPA"): its real procurement
-- contact was added 6 days after we first ingested it, and our
-- nightly ingestion (which only ever queries a postedFrom/postedTo
-- WINDOW, and this notice had since aged out of SAM.gov's own active
-- search index) could never have re-discovered it.
--
-- Reuses ingestion_jobs (a new `job_type` column, same reasoning
-- migration 044 already gives for reusing backup_jobs for restore
-- drills) rather than a new table — GET /ingestion/jobs already
-- shows this history with zero code changes.
-- ============================================================

alter table ingestion_jobs
    add column job_type text not null default 'ingestion'
        check (job_type in ('ingestion', 'refresh'));

comment on column ingestion_jobs.job_type is
    'ingestion = a normal postedFrom/postedTo pull of new notices; refresh = re-checking already-stored notices for amendments (2026-09, app/sam_gov_refresh.py). records_ingested holds "records touched" either way.';
