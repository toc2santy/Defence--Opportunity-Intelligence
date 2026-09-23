-- ============================================================
-- Programme procurement contacts — the contact half of the
-- original roadmap's engine 09 (Engagement Intelligence).
--
-- DELIBERATE SCOPE LIMIT, and the reason for it:
-- These fields hold PERSONAL DATA. Live samples are named
-- individuals with personal work addresses, not just generic
-- inboxes:
--     CanadaBuys  "Andre Champagne"  andre.champagne@dcc-cdc.gc.ca
--     UK FaTS     "MIRELA SIMIONOV"  mirela.simionov@argyll-bute.gov.uk
-- (some rows are team inboxes like "Corporate Procurement Team",
-- but many are not, and the two cannot be reliably separated.)
--
-- Governments publish these SO THAT suppliers can ask about that
-- specific tender. Storing them against the specific programme and
-- showing them on that programme's own opportunity is the same
-- purpose the publisher intended.
--
-- Building an aggregated, searchable CONTACT DIRECTORY across
-- programmes ("every contact at DND") would be a materially
-- different processing activity — closer to a data-broker product
-- than to opportunity intelligence — and is NOT built here on
-- purpose. Deliberately absent, and to be added only after real
-- GDPR / India DPDP review:
--   * any endpoint that lists or searches contacts independently
--     of the programme they belong to
--   * any aggregation of contacts by organisation
--   * any export of contacts in bulk
-- Columns live on `programmes` rather than in their own
-- `contacts` table precisely so there is nothing that invites
-- being queried as a standalone directory.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/017_programme_contacts.sql
-- ============================================================

alter table programmes add column if not exists contact_name  text;
alter table programmes add column if not exists contact_email text;
alter table programmes add column if not exists contact_phone text;

-- Deliberately NO index on contact_email / contact_name. An index
-- exists to make lookup-by-that-column fast, and fast lookup by
-- contact is exactly the directory use case this migration is
-- scoped to avoid. Contacts are only ever read via their own
-- programme row, which is already indexed by primary key.
