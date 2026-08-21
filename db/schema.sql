-- ============================================================
-- DEFENCE OPPORTUNITY INTELLIGENCE — FOUNDATION SCHEMA
-- Phase 0: tenancy, auth, source/evidence framework, core
-- domain entities. Everything downstream (matching, scoring,
-- reports) reads and writes through this schema.
-- ============================================================

create extension if not exists "pgcrypto";  -- gen_random_uuid()

-- ------------------------------------------------------------
-- TENANCY & AUTH
-- ------------------------------------------------------------

create table tenants (
    id              uuid primary key default gen_random_uuid(),
    name            text not null,
    plan            text not null default 'starter'  -- starter | professional | enterprise
                        check (plan in ('starter','professional','enterprise')),
    created_at      timestamptz not null default now()
);

create table roles (
    id              uuid primary key default gen_random_uuid(),
    name            text not null unique  -- admin | analyst | bd_user | viewer
);

insert into roles (name) values ('admin'), ('analyst'), ('bd_user'), ('viewer')
on conflict (name) do nothing;

create table users (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    email           citext not null unique,
    password_hash   text not null,               -- argon2id
    role_id         uuid not null references roles(id),
    is_active       boolean not null default true,
    created_at      timestamptz not null default now()
);

create index idx_users_tenant on users(tenant_id);

-- Every mutating action gets logged. This table is append-only
-- at the application layer (no UPDATE/DELETE grants).
create table audit_log (
    id              bigserial primary key,
    tenant_id       uuid not null references tenants(id),
    user_id         uuid references users(id),
    action          text not null,               -- e.g. 'opportunity.stage_changed'
    entity_type     text not null,
    entity_id       uuid,
    before_state     jsonb,
    after_state      jsonb,
    created_at      timestamptz not null default now()
);
create index idx_audit_tenant_time on audit_log(tenant_id, created_at desc);

-- ------------------------------------------------------------
-- SOURCE & EVIDENCE FRAMEWORK
-- This is the backbone that makes every later intelligence
-- claim traceable. Nothing in the domain tables below should
-- carry a "score" or "classification" without an evidence row
-- pointing at how it was derived.
-- ------------------------------------------------------------

create table sources (
    id              uuid primary key default gen_random_uuid(),
    name            text not null,                -- 'SRIJAN Portal', 'SAM.gov', 'Customer-provided'
    source_type     text not null
                        check (source_type in
                            ('government_portal','licensed_dataset','public_tender',
                             'company_filing','news','customer_provided','internal_analyst')),
    url             text,
    trust_level     text not null default 'medium'
                        check (trust_level in ('high','medium','low')),
    terms_notes     text,                          -- scraping/ToS constraints, for compliance review
    created_at      timestamptz not null default now()
);

create table evidence (
    id                  uuid primary key default gen_random_uuid(),
    source_id           uuid not null references sources(id),
    related_entity_type text not null,             -- 'programme' | 'opportunity' | 'capability' | ...
    related_entity_id   uuid not null,
    claim               text not null,             -- plain-language statement this evidence supports
    evidence_status     text not null
                            check (evidence_status in
                                ('verified','reported','estimated','ai_inferred','unverified')),
    confidence          text not null default 'medium'
                            check (confidence in ('high','medium','low')),
    captured_at         timestamptz not null default now(),
    reviewed_by         uuid references users(id),  -- analyst who validated it (human-in-the-loop)
    reviewed_at         timestamptz
);
create index idx_evidence_entity on evidence(related_entity_type, related_entity_id);

create table ingestion_jobs (
    id              uuid primary key default gen_random_uuid(),
    source_id       uuid not null references sources(id),
    status          text not null default 'pending'
                        check (status in ('pending','running','succeeded','failed')),
    started_at      timestamptz,
    finished_at     timestamptz,
    records_ingested integer default 0,
    error           text
);

-- ------------------------------------------------------------
-- ORGANIZATIONS
-- One table for every non-tenant entity in the ecosystem —
-- OEMs, customers, competitors, integrators, government bodies —
-- distinguished by org_type. Avoids five near-identical tables.
-- ------------------------------------------------------------

create table organizations (
    id              uuid primary key default gen_random_uuid(),
    name            text not null,
    org_type        text not null
                        check (org_type in
                            ('oem','customer','competitor','integrator',
                             'government_body','dpsu','prime_contractor')),
    country         text,
    classification  text not null default 'public'
                        check (classification in
                            ('public','customer_provided','licensed','internal','restricted')),
    notes           text,
    created_at      timestamptz not null default now()
);
create index idx_org_type on organizations(org_type);

create table contacts (
    id              uuid primary key default gen_random_uuid(),
    organization_id uuid not null references organizations(id) on delete cascade,
    name            text,                          -- nullable: role-level contact without a name yet
    role_title      text,
    contact_type    text not null
                        check (contact_type in ('business_development','technical','procurement')),
    email           text,
    phone           text,
    lawful_basis    text not null default 'business_context'
                        check (lawful_basis in ('business_context','public_listing','customer_provided')),
    created_at      timestamptz not null default now()
);

-- ------------------------------------------------------------
-- CAPABILITY TAXONOMY (tenant-agnostic master list, curated by
-- your own analysts — this replaces the 11-line keyword lookup
-- table from the prototype)
-- ------------------------------------------------------------

create table capability_taxonomy (
    id              uuid primary key default gen_random_uuid(),
    code            text not null unique,          -- 'EW.COMMS.TACTICAL'
    label           text not null,                 -- 'Secure Tactical Communication Subsystem'
    sector          text not null,                 -- 'Electronic Warfare', 'UAV/UAS', ...
    parent_id       uuid references capability_taxonomy(id),
    description     text
);

-- ------------------------------------------------------------
-- TENANT-OWNED DOMAIN DATA
-- Everything below is scoped to a tenant via RLS.
-- ------------------------------------------------------------

create table products (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    name            text not null,
    description     text,
    trl             smallint check (trl between 1 and 9),
    certifications  jsonb not null default '[]',
    created_by      uuid references users(id),
    created_at      timestamptz not null default now()
);
create index idx_products_tenant on products(tenant_id);

create table product_capabilities (
    product_id       uuid not null references products(id) on delete cascade,
    capability_id    uuid not null references capability_taxonomy(id),
    confidence       text not null default 'medium'
                        check (confidence in ('high','medium','low')),
    classified_by    text not null default 'analyst'
                        check (classified_by in ('analyst','ai_suggested')),
    evidence_id      uuid references evidence(id),
    primary key (product_id, capability_id)
);

create table programmes (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null,
    country             text not null,
    organization_id     uuid references organizations(id),   -- owning body
    platform            text,
    capability_required uuid references capability_taxonomy(id),
    stage               text
                            check (stage in
                                ('early_concept','requirement_defined','rfi_issued',
                                 'rfp_issued','contract_awarded','in_service')),
    source_id           uuid references sources(id),
    last_updated        timestamptz not null default now()
);

create table markets (
    id                  uuid primary key default gen_random_uuid(),
    country             text not null unique,
    attractiveness_score smallint check (attractiveness_score between 0 and 100),
    entry_complexity    text check (entry_complexity in ('low','medium','high')),
    procurement_structure text,
    partner_ecosystem   text,
    evidence_id         uuid references evidence(id),
    last_updated        timestamptz not null default now()
);

create table opportunities (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    product_id      uuid not null references products(id),
    programme_id    uuid references programmes(id),
    organization_id uuid references organizations(id),        -- target customer/OEM
    stage           text not null default 'lead'
                        check (stage in
                            ('lead','qualified','technical_discussion','nda','demo',
                             'trial','evaluation','rfi','rfp','proposal','negotiation',
                             'won','lost')),
    score           smallint check (score between 0 and 100),  -- null until Phase 3 computes it
    confidence      text check (confidence in ('high','medium','low')),
    next_action     text,
    owner_user_id   uuid references users(id),
    due_date        date,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index idx_opp_tenant on opportunities(tenant_id);
create index idx_opp_stage on opportunities(tenant_id, stage);

-- Transparent, per-opportunity scoring breakdown — this is what
-- makes the "adjustable weights" UI real instead of decorative.
create table opportunity_score_factors (
    opportunity_id  uuid not null references opportunities(id) on delete cascade,
    factor_name     text not null,       -- 'capability_fit', 'market_attractiveness', ...
    weight_pct      smallint not null check (weight_pct between 0 and 100),
    raw_value       numeric,             -- underlying measured value before weighting
    contribution    numeric,             -- weight_pct * raw_value, for auditability
    primary key (opportunity_id, factor_name)
);

create table engagement_log (
    id              uuid primary key default gen_random_uuid(),
    opportunity_id  uuid not null references opportunities(id) on delete cascade,
    stage_from      text,
    stage_to        text not null,
    note            text,
    updated_by      uuid references users(id),
    updated_at      timestamptz not null default now()
);

-- ============================================================
-- ROW-LEVEL SECURITY — tenant isolation enforced at the DB,
-- not just in application code.
-- ============================================================

alter table products enable row level security;
alter table opportunities enable row level security;
alter table opportunity_score_factors enable row level security;
alter table engagement_log enable row level security;

-- The API sets `set_config('app.current_tenant', '<uuid>', true)`
-- once per request, right after authenticating the JWT.
create policy tenant_isolation_products on products
    using (tenant_id = current_setting('app.current_tenant', true)::uuid);

create policy tenant_isolation_opportunities on opportunities
    using (tenant_id = current_setting('app.current_tenant', true)::uuid);

create policy tenant_isolation_score_factors on opportunity_score_factors
    using (
        opportunity_id in (
            select id from opportunities
            where tenant_id = current_setting('app.current_tenant', true)::uuid
        )
    );

create policy tenant_isolation_engagement_log on engagement_log
    using (
        opportunity_id in (
            select id from opportunities
            where tenant_id = current_setting('app.current_tenant', true)::uuid
        )
    );

-- ============================================================
-- SEED: a starter capability taxonomy so Phase 1 has something
-- real to classify against instead of an 11-line keyword table.
-- Extend this with actual domain expert input before launch.
-- ============================================================

insert into capability_taxonomy (code, label, sector) values
    ('COMMS.SECURE.TACTICAL', 'Secure Tactical Communication Subsystem', 'Secure Communications'),
    ('UAV.INTEGRATION', 'Tactical UAV Systems Subsystem', 'UAV / UAS'),
    ('SENSING.RADAR', 'Radar & Sensing Subsystem', 'Radar'),
    ('EW.GENERAL', 'Electronic Warfare Subsystem', 'Electronic Warfare'),
    ('CYBER.DEFENCE', 'Cyber Defence Capability', 'Cyber Defence'),
    ('C4ISR.INTEGRATION', 'C4ISR Integration Capability', 'C4ISR'),
    ('NAVAL.SYSTEMS', 'Naval Systems Subsystem', 'Naval Systems'),
    ('SENSOR.ELECTRO_OPTIC', 'Electro-Optic / Sensor Subsystem', 'Electro-Optics'),
    ('AUTONOMY.GENERAL', 'Autonomous Systems Capability', 'AI / Autonomous Systems')
on conflict (code) do nothing;

-- Added after first live run: audit_log carries tenant_id and
-- should be tenant-isolated like the other tenant-owned tables.
alter table audit_log enable row level security;
create policy tenant_isolation_audit_log on audit_log
    using (tenant_id = current_setting('app.current_tenant', true)::uuid);
