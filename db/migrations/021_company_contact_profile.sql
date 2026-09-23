-- ============================================================
-- Company + contact profile fields, collected at signup.
--
-- Before this, signup asked for exactly three things (company_name,
-- email, password) — enough to create a working account, but not
-- enough to ever greet someone by name or pre-fill the Contact tab's
-- forms, which is what this migration is for. Both new column groups
-- are optional except full_name (the one field genuinely needed to
-- address a real person, matching the existing required-password
-- discipline) — nothing here should block a fast signup that a real
-- prospect abandons over friction.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/021_company_contact_profile.sql
-- ============================================================

alter table users add column if not exists full_name text;
alter table users add column if not exists title     text;  -- job title/role, e.g. "Business Development Lead"
alter table users add column if not exists phone      text;

alter table tenants add column if not exists website  text;
alter table tenants add column if not exists country  text;
alter table tenants add column if not exists phone    text;
