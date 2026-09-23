-- ============================================================
-- Email verification on signup (2026-09) — a real, common gap: any
-- string that merely PASSES Pydantic's EmailStr format check could
-- be entered at signup with no confirmation the signer-upper actually
-- controls that inbox. This closes it: a signup now sends a real
-- verification link, and `users.email_verified` records whether it
-- was ever clicked.
--
-- `email_verified` defaults to TRUE at the column level (not FALSE)
-- deliberately — this project's real, already-in-use tenants (Syas Ai
-- and Automation, M/s Alpha_Elsec) predate this feature and have been
-- actively using their real email for login/password-reset for weeks;
-- treating them as suddenly "unverified" would be both incorrect
-- (they demonstrably do control those inboxes) and disruptive. The
-- signup route's own INSERT explicitly overrides this default to
-- FALSE for every brand-new account going forward — see main.py.
--
-- `email_verification_tokens` follows the exact same shape as the
-- already-existing `password_reset_tokens` (raw token never stored,
-- only its SHA-256 hash; `used_at` marks consumption rather than
-- deleting the row, so a reused/expired link fails the same way a
-- stale password-reset link already does).
-- ============================================================

alter table users add column email_verified boolean not null default true;

create table email_verification_tokens (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users(id) on delete cascade,
    token_hash text not null,
    expires_at timestamptz not null,
    used_at timestamptz,
    created_at timestamptz not null default now()
);

create index idx_email_verification_tokens_user on email_verification_tokens(user_id);

comment on table email_verification_tokens is
    'One-time email-confirmation links (2026-09) — same shape as password_reset_tokens: raw token never stored, only its hash.';
