-- The application connects as this role, never as `postgres`.
-- RLS policies apply to it; a superuser bypasses RLS entirely,
-- which would silently defeat the tenant-isolation guarantee.

create extension if not exists citext;

do $$
begin
    if not exists (select from pg_catalog.pg_roles where rolname = 'doi_app') then
        create role doi_app with login password 'changeme';
    end if;
end
$$;

grant connect on database doi to doi_app;
grant usage on schema public to doi_app;

-- Applies automatically to tables/sequences created afterward by
-- `postgres` (schema.sql runs immediately after this file), so
-- doi_app has working privileges without a manual post-schema step.
alter default privileges for role postgres in schema public
    grant select, insert, update, delete on tables to doi_app;
alter default privileges for role postgres in schema public
    grant usage, select on sequences to doi_app;
