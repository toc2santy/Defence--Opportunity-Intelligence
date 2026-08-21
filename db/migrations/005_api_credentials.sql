-- ============================================================
-- Credential expiry tracking — so a key expiring silently doesn't
-- quietly break ingestion one day with no warning. Apply the same
-- way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/005_api_credentials.sql
-- ============================================================

create table api_credentials (
    id              uuid primary key default gen_random_uuid(),
    credential_name text not null unique,       -- e.g. 'SAM_GOV_API_KEY'
    issued_at       date,
    expires_at      date not null,
    notes           text,
    updated_at      timestamptz not null default now()
);

-- Seeded with the real key obtained during this session. Update
-- this row (via PATCH /ingestion/sam-gov/key-status) whenever the
-- key is rotated — don't hand-edit this migration file again after
-- the first run, since migrations are meant to be one-time.
insert into api_credentials (credential_name, issued_at, expires_at, notes)
values (
    'SAM_GOV_API_KEY',
    '2026-08-18',
    '2026-11-15',
    'Personal/non-federal tier SAM.gov Public API Key. Stated validity: 89 days from issue. Renew via SAM.gov Account Details before expiry, then update this record with the new expiry date.'
)
on conflict (credential_name) do nothing;
