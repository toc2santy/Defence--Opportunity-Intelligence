-- ============================================================
-- Per-account failed-login lockout (2026-09) — the security-control
-- item recommended and built right after MFA. Rate limiting
-- (LOGIN_RATE_LIMIT, slowapi) is keyed by IP and already existed,
-- but that alone does not stop a distributed credential-stuffing
-- attempt (many IPs, one targeted account) or a single attacker
-- rotating IPs. This is the account-specific complement: N wrong
-- passwords in a row locks THAT account for a bounded window,
-- regardless of which IP the attempts came from.
--
-- Deliberately kept as two plain columns on `users` rather than a
-- separate attempts-log table — a login lockout only ever needs to
-- answer "is this account locked right now, and how many recent
-- wrong attempts has it had," never "show me the full history of
-- every attempt" (that's what audit_log's own event-log shape is
-- for, and failed logins are deliberately NOT written there — see
-- app/main.py's login route comment on why a bare count is enough
-- and a full log of failed passwords would just be extra risk for
-- no real benefit).
--
-- Kept as ONE statement deliberately (see CLAUDE.md's "Isolated test
-- stack" / migration 0045 entries for why): this project's async
-- alembic setup cannot execute a file with more than 2 top-level
-- SQL statements.
--
-- Apply:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/046_login_lockout.sql
-- ============================================================

alter table users
    add column failed_login_attempts integer not null default 0,
    add column locked_until timestamptz;
