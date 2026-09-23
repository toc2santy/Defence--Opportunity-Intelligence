-- ============================================================
-- Captures contract award value — needed for the Fit & Feasibility
-- Score's "value-tier fit" parameter (P3), found to be a real,
-- fixable gap while designing that score (2026-09).
--
-- WHY THIS IS NEEDED: contract_awards currently stores only
-- (programme_id, winner_organization_id, source_id) — no amount at
-- all. Two sources' raw responses genuinely carry a real AWARD-LEVEL
-- total and it is simply discarded on ingest today:
--   * AusTender — contracts[].value: {currency, amount}, confirmed
--     live (e.g. "AUD" / "318617.20").
--   * SECOP II Colombia — valor_total_adjudicacion, a real column
--     already named as a constant (COL_AWARD_VALUE in
--     app/colombia_normalize.py) but never actually read.
-- Checked and deliberately NOT wired for two other sources rather
-- than guessed at:
--   * DNCP Paraguay only publishes value at the ITEM level (a unit
--     price per line item, confirmed live on aircraft-maintenance
--     line items) — there is no single award-level total to store
--     without inventing an aggregation rule, so it's left out until
--     there's a real reason to design that rule properly.
--   * CanadaBuys' award-notice file's own column list
--     (app/canada_buys_normalize.py) has no value/amount column at
--     all — an earlier draft of this migration claimed one existed;
--     that was wrong and has been corrected here rather than shipped.
-- Not every award-capable source publishes a value at all (TED, UK
-- Find a Tender, ProZorro have not been re-checked for this
-- specifically) — value_amount is therefore nullable, and a NULL
-- here means "not published by this source", never "zero".
--
-- WHY NOT A SEPARATE TABLE: an award's value is a property of the
-- award itself, one-to-one with the (programme, winner) pair
-- contract_awards already models — no new relationship, just two
-- columns already-arriving data was falling on the floor for.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/040_contract_award_value.sql
-- ============================================================

alter table contract_awards add column if not exists value_amount numeric;
alter table contract_awards add column if not exists value_currency text;

comment on column contract_awards.value_amount is
    'The award''s stated value, exactly as published by the source. NULL means the source did not publish one — never assume 0 or estimate here; do that at query time, not by writing a guessed value into this column.';
comment on column contract_awards.value_currency is
    'ISO-ish currency code as printed by the source (e.g. AUD, USD, PYG) — not normalized/converted to a common currency, since FX-rate conversion is a presentation-time decision, not an ingestion-time one.';
