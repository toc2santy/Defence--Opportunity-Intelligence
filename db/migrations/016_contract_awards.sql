-- ============================================================
-- Contract awards — OEM Intelligence (the original roadmap's
-- Phase 4, alongside Customer Intelligence in migration 015's
-- companion work).
--
-- A separate table rather than a column on `programmes`, because an
-- award is a distinct fact about a DIFFERENT organisation than the
-- one on the programme (the buyer) — and, confirmed against live
-- TED data, a single award notice can name several winners at once
-- (one per lot), so it is genuinely one-to-many, not one-to-one.
--
-- Winning organisations are stored in the existing `organizations`
-- table with org_type = 'oem' (that value already existed in the
-- schema's check constraint, unused until now) — no new company
-- table needed.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/016_contract_awards.sql
-- ============================================================

create table if not exists contract_awards (
    id                      uuid primary key default gen_random_uuid(),
    programme_id            uuid not null references programmes(id) on delete cascade,
    winner_organization_id  uuid not null references organizations(id),
    source_id               uuid references sources(id),
    created_at              timestamptz not null default now(),
    -- One row per (programme, winner) pair — re-ingesting the same
    -- award notice must not create duplicate award rows, same
    -- idempotency guarantee the programmes table itself has via its
    -- (source_id, external_ref) unique index.
    unique (programme_id, winner_organization_id)
);

create index if not exists idx_contract_awards_winner
    on contract_awards (winner_organization_id);

create index if not exists idx_contract_awards_programme
    on contract_awards (programme_id);
