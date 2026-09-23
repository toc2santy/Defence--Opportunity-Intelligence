-- ============================================================
-- Fixes the real root cause behind buyer/org duplication — not a
-- fuzzy-matching feature, a genuine data-integrity gap.
--
-- WHAT WAS FOUND, and how: a Report Intel review into Customer
-- Intelligence's buyer aggregation quality went looking for
-- duplicate organisation rows live, rather than assuming the
-- earlier-flagged "MoD/Ministère de la Défense/Minister of National
-- Defence" pattern was a real problem — it wasn't (each names a
-- genuinely different country's ministry). What WAS real, found by
-- checking for exact- and whitespace-duplicate names within the same
-- org_type, was small but genuine:
--
--   1. A byte-for-byte duplicate: "військова частина А5063"
--      (Ukrainian military unit) inserted twice, same org_type.
--      Confirmed via hex dump — not a visual illusion, a real race:
--      two ingestion calls both ran their "does this name already
--      exist" SELECT before either INSERT had committed, so both
--      concluded "no" and both inserted.
--   2. A whitespace duplicate: SAM.gov's own fullParentPathName field
--      inserted a DLA Aviation Ogden office with a double space in
--      one record and a single space in another — same real office,
--      two rows.
--
-- THE ROOT CAUSE both share: every one of this project's 8 ingestion
-- modules ran its own private "SELECT — if found, return; else
-- INSERT" against `organizations`, and NOTHING in the schema ever
-- stopped two such sequences from racing each other. This migration
-- fixes the data (merges the two known duplicate pairs) and then
-- makes the race structurally impossible (a real UNIQUE constraint,
-- which app/ingestion_common.py's new shared get_or_create_organization
-- now upserts against with ON CONFLICT — see that module for the code
-- half of this fix).
--
-- WHY NORMALIZATION IS WHITESPACE-ONLY, DELIBERATELY: collapsing
-- repeated whitespace and trimming is safe because it can only ever
-- unify two strings that a human reads as identical. Anything beyond
-- that (accent-folding, fuzzy/semantic matching across differently-
-- worded names) risks merging two REAL, DIFFERENT buyers into one —
-- worse than the fragmentation it would fix — and was deliberately
-- rejected in favour of leaving that as a human decision, not this
-- migration's.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/035_organizations_dedup.sql
-- ============================================================

begin;

-- Step 1: normalize every name in place (collapse internal whitespace
-- runs to one space, trim leading/trailing). This alone is what turns
-- the DLA Aviation Ogden pair into a true duplicate the next step can
-- find and merge.
update organizations
set name = regexp_replace(trim(name), '\s+', ' ', 'g')
where name <> regexp_replace(trim(name), '\s+', ' ', 'g');

-- Step 2: for every (name, org_type) group that is now a duplicate,
-- keep the oldest row (lowest created_at, ties broken by id) and
-- repoint every foreign key that pointed at any of the others onto
-- it, then delete the now-unreferenced duplicates. Generic across all
-- four tables that can reference organizations — not hand-written per
-- known duplicate, so it also catches ones this review's manual
-- checks did not happen to sample.
do $$
declare
    dup record;
    keeper uuid;
    loser uuid;
begin
    for dup in
        select name, org_type, (array_agg(id order by created_at, id))[1] as keeper_id,
               (array_agg(id order by created_at, id))[2:] as loser_ids
        from organizations
        group by name, org_type
        having count(*) > 1
    loop
        keeper := dup.keeper_id;
        foreach loser in array dup.loser_ids loop
            update programmes set organization_id = keeper where organization_id = loser;
            update opportunities set organization_id = keeper where organization_id = loser;
            update contract_awards set winner_organization_id = keeper where winner_organization_id = loser;
            update contacts set organization_id = keeper where organization_id = loser;
            delete from organizations where id = loser;
        end loop;
    end loop;
end $$;

-- Step 3: make the race structurally impossible from here on.
alter table organizations add constraint organizations_name_org_type_key unique (name, org_type);

commit;
