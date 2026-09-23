-- ============================================================
-- Password reset tokens — "Forgot password?" on the login screen.
--
-- A separate table rather than columns on `users`, for the same
-- reason `evidence`/`audit_log` are separate tables: a reset request
-- is an EVENT with its own lifecycle (issued, used-or-not, expires),
-- not an attribute of the user row. Keeping it separate also means a
-- second reset request doesn't have to clobber or reuse the first
-- one's row — both simply exist, and only an unused, unexpired token
-- is ever honoured.
--
-- token_hash, not the raw token: the same reasoning as password_hash
-- on `users` — a stolen DB dump should not hand out live reset
-- tokens. The raw token only ever exists in the email sent to the
-- user and the URL they click; the DB only ever sees its SHA-256.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/023_password_reset_tokens.sql
-- ============================================================

create table if not exists password_reset_tokens (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references users(id) on delete cascade,
    token_hash  text not null unique,
    expires_at  timestamptz not null,
    used_at     timestamptz,
    created_at  timestamptz not null default now()
);

create index if not exists idx_password_reset_tokens_user on password_reset_tokens(user_id);

-- Fast lookup by the hash a reset link presents back — the whole
-- point of the endpoint this table serves.
create index if not exists idx_password_reset_tokens_hash on password_reset_tokens(token_hash);
