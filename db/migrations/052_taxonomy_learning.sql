-- ============================================================
-- Self-expanding taxonomy (2026-10) — the taxonomy used to grow only
-- when someone wrote a migration. Measured live: 11,565 programmes,
-- only ~53% reachable by ANY capability (code mapping or keyword); the
-- rest were never even candidates for a product search.
--
-- app/taxonomy_learning.py now mines the stored tenders for
--   (a) KEYWORDS  — title phrases that are strongly specific to one
--       capability's code-mapped tenders (high-precision ones are
--       added automatically at weight 1; the rest wait for review), and
--   (b) CODES     — classification codes not yet mapped to anything
--       whose tender titles keep scoring for one capability (never
--       automatic: a code pulls EVERY tender carrying it into a
--       product's candidate set, so a human approves it).
--
-- Keywords only ever come from PUBLIC tender text — never from a
-- tenant's product description — because the taxonomy is global and
-- shared by every tenant (a keyword learned from one customer's
-- product text would leak it to all the others).
-- ============================================================

alter table capability_taxonomy_keywords
    add column origin text not null default 'curated'
        check (origin in ('curated', 'learned'));

comment on column capability_taxonomy_keywords.origin is
    'curated = written by a person / migration; learned = added by app/taxonomy_learning.py (always weight 1 until a platform admin raises it).';

create table taxonomy_learning_suggestions (
    id              uuid primary key default gen_random_uuid(),
    kind            text not null check (kind in ('keyword', 'code')),
    capability_id   uuid not null references capability_taxonomy(id) on delete cascade,
    value           text not null,
    source_name     text,
    status          text not null default 'pending'
                        check (status in ('pending', 'auto_added', 'approved', 'rejected')),
    support_count   integer not null,
    distinct_buyers integer not null default 0,
    precision_pct   numeric(5, 1),
    lift            numeric(10, 1),
    examples        jsonb not null default '[]'::jsonb,
    keyword_id      uuid references capability_taxonomy_keywords(id) on delete set null,
    first_seen      timestamptz not null default now(),
    last_seen       timestamptz not null default now(),
    decided_by      uuid,
    decided_at      timestamptz,
    unique (kind, capability_id, value)
);

create index idx_taxonomy_learning_status on taxonomy_learning_suggestions (status, kind);

alter table backup_jobs
    drop constraint backup_jobs_job_type_check,
    add constraint backup_jobs_job_type_check
        check (job_type in ('backup', 'restore_drill', 'retention_purge', 'taxonomy_learning'));
