-- ============================================================
-- Scheduled ingestion + "what's new" tracking.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/006_scheduled_ingestion_and_notifications.sql
-- ============================================================

-- This is the actual mechanism behind "you saw this first" — a
-- server-side, tenant-owned checkpoint that only advances when the
-- tenant genuinely calls GET /opportunities/new. Client-supplied
-- "since" timestamps could be faked or manipulated; this can't be,
-- because the tenant never writes to it directly.
alter table tenants add column if not exists opportunities_last_viewed_at timestamptz;
