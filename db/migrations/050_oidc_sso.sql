-- ============================================================
-- SSO via OIDC (2026-09) — "Sign in with Google" / "Sign in with
-- Microsoft" / any standard OIDC provider, additive to (never
-- replacing) email+password login.
--
-- A separate table, not columns bolted onto `users`, because the
-- relationship is genuinely one-to-many: the same person could sign
-- in via Google AND Microsoft, or a support/incident need to revoke
-- just ONE linked identity without touching the account itself.
--
-- (provider, subject) is the real identity — `subject` is the OIDC
-- spec's own stable, permanent identifier for an account at that
-- provider (never reused, unlike email, which a provider account
-- could theoretically change). `email` is stored too, but only ever
-- used at FIRST link time (auto-linking to an existing password
-- account with the same verified email) — never trusted again after
-- that for matching, exactly so a changed email at the provider can't
-- silently re-point who a login authenticates as.
-- ============================================================

create table oidc_identities (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users(id) on delete cascade,
    provider text not null,
    subject text not null,
    email text not null,
    created_at timestamptz not null default now(),
    unique (provider, subject)
);

create index idx_oidc_identities_user on oidc_identities(user_id);

comment on table oidc_identities is
    'Linked OIDC (SSO) identities — (provider, subject) is the real, permanent identity; email is only ever consulted at first-link time (2026-09).';
