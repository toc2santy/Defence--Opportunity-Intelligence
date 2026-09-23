-- ============================================================
-- MFA (TOTP) — the second security-controls item recommended
-- alongside Eligibility/Export-Control Phase 1 (see CLAUDE.md,
-- 2026-09). Login today is password-only; a compromised password
-- alone gives full access to a tenant's tender/pricing data, with
-- no second factor to stop it.
--
-- mfa_secret stores a Fernet-ENCRYPTED TOTP secret, not plaintext —
-- a dedicated MFA_ENCRYPTION_KEY (separate from BACKUP_ENCRYPTION_KEY
-- so the two secrets' blast radius stays independent), same "hash/
-- encrypt at rest" discipline as password_hash and
-- password_reset_tokens.token_hash already use in this schema.
--
-- mfa_backup_codes stores SHA-256 HASHES of one-time recovery codes,
-- never the plaintext codes themselves (shown to the user exactly
-- once, at generation time, same as a password-reset token's raw
-- value never being persisted). A used code is removed from the
-- array rather than flagged, so "is this code still valid" is a
-- plain array-membership check with no extra state to go stale.
--
-- mfa_secret is set (encrypted) as soon as setup starts, but
-- mfa_enabled stays false until the user proves they can actually
-- generate a valid code with it (POST /auth/mfa/enable) — an
-- unconfirmed secret must never gate login, or a broken authenticator
-- app setup would lock someone out of their own account.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/045_mfa.sql
-- ============================================================

alter table users
    add column mfa_secret text,
    add column mfa_enabled boolean not null default false,
    add column mfa_backup_codes text[],
    add column mfa_enrolled_at timestamptz;

comment on column users.mfa_secret is
    'Fernet-encrypted (MFA_ENCRYPTION_KEY) TOTP secret. Set on /auth/mfa/setup; only trusted as real once mfa_enabled is true.';
comment on column users.mfa_backup_codes is
    'SHA-256 hashes of one-time recovery codes, shown to the user once at enrollment. A used code is removed from the array, not flagged.';
comment on column users.mfa_enabled is
    'Only true once the user has proven (via /auth/mfa/enable) they can generate a valid code from mfa_secret — an unconfirmed secret never gates login.';
