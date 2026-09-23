-- ============================================================
-- Company Compliance Profile — the identity a defence buyer needs
-- to trust before dealing with a vendor: GST/PAN/TAN/IEC, a logo,
-- and a public-but-limited "pitch card" that can be shared with
-- anyone (WhatsApp, email, a link) without exposing sensitive
-- registration numbers to an unauthenticated viewer.
--
-- share_token is deliberately a SEPARATE random value from tenant
-- id — even though the UUID id is already unguessable, a dedicated
-- token can be regenerated to instantly revoke a previously-shared
-- link (e.g. if it leaked somewhere it shouldn't have) without
-- touching the tenant's real id, which every other table references.
--
-- logo_data_url stores a data: URL directly (base64), capped at the
-- application layer (see main.py) — there is no object storage
-- (S3-equivalent) in this stack yet, and a small logo comfortably
-- fits inline. Revisit if logos need to be large or served outside
-- an API response.
--
-- custom_fields is a plain jsonb map of {label: value} for whatever
-- a company wants to add beyond the named compliance fields
-- (a specific certification, a registration number this schema
-- didn't anticipate) — deliberately unstructured, since enumerating
-- every possible compliance document a defence buyer might ask for
-- is not something to guess at up front.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/022_company_compliance_profile.sql
-- ============================================================

alter table tenants add column if not exists gst_number   text;
alter table tenants add column if not exists pan_number   text;
alter table tenants add column if not exists tan_number   text;
alter table tenants add column if not exists iec_license  text;
alter table tenants add column if not exists tagline      text;
alter table tenants add column if not exists logo_data_url text;
alter table tenants add column if not exists share_token  text unique;
alter table tenants add column if not exists custom_fields jsonb not null default '{}'::jsonb;

-- Every existing tenant gets a share token immediately, not just new
-- signups — otherwise every account created before this migration
-- would have no way to generate one without an extra code path.
update tenants set share_token = encode(gen_random_bytes(16), 'hex') where share_token is null;
