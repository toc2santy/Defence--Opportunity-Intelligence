-- ============================================================
-- Programme apply/detail link — the piece "Path to Contract" needs
-- most and the platform never actually stored.
--
-- Every normalizer already computes this (ted_eu, uk_ft, canada_buys,
-- cppp_india, sam_gov all set a `ui_link` field), but `programmes`
-- had no column for it, so it was silently discarded at ingestion
-- time on every source, since day one. Found while building the
-- "Path to Contract" panel — a feature meant to help a user actually
-- reach the tender was missing the one field that gets them there.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/018_programme_ui_link.sql
-- ============================================================

alter table programmes add column if not exists ui_link text;
