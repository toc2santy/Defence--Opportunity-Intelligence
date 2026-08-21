# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Defence Opportunity Intelligence — a multi-tenant platform that ingests real
government procurement data (SAM.gov US, UK Find a Tender, EU TED), classifies
a tenant's product capabilities against a taxonomy, and matches products to
live programmes/opportunities. See `README.md` for the full engineering
status — it is written as an honest, current record of what's built, what's
partial, and what's deliberately deferred; read it before assuming a feature
exists.

Not a git repository (no `.git`). Numbered `defence-oi-patchNN-*.zip` files at
the repo root are historical patch bundles from prior work sessions — not
something to generate or maintain, and not the current source of truth (the
files in `api/`, `db/` are).

## Running it locally

```bash
cp .env.example .env
# fill in JWT_SECRET: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# leave SAM_GOV_API_KEY blank unless running live SAM.gov ingestion

docker compose up --build
# API on http://localhost:8000, Postgres on localhost:5432
```

`db/schema.sql` and `db/roles.sql` are auto-applied by Postgres's
`docker-entrypoint-initdb.d` on first container start. Everything after that
baseline goes through Alembic:

```bash
# fresh DB:
docker compose exec api alembic upgrade head

# DB that already has the schema applied by hand (this project's own dev DB):
docker compose exec api alembic stamp 0006_scheduled_ingestion
```

New schema change:
```bash
docker compose exec api alembic revision -m "description"
# hand-write upgrade()/downgrade() — no autogenerate (no ORM models to diff, see below)
docker compose exec api alembic upgrade head
```

## Tests

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v                          # full suite
pytest tests/test_matching_scoring_unit.py -v      # single file
pytest tests/test_matching_scoring_unit.py::test_name -v   # single test
```

Requires `docker compose up` already running — tests hit the real API over
HTTP (`localhost:8000`) and, for setup only, the real DB directly as
`postgres` (bypasses RLS, safe since setup never asserts through that
connection). No mocking. Tests are split:

- `test_*_unit.py` — pure logic (scoring, normalization, matching), no DB/network.
- everything else — integration, hits the live stack over HTTP.

Several tests are opt-in and skip by default to avoid hammering live
government APIs or double-counting shared rate-limit budgets:
- `test_sam_gov_live.py` — needs a real `SAM_GOV_API_KEY`.
- `test_uk_ft_trigger.py` / `test_ted_eu_trigger.py`-style live checks — gated
  behind `RUN_LIVE_UK_TEST=1` / equivalent env flags.
- **Don't run `test_sam_gov_live.py` and `test_rate_limiting.py` in the same
  sweep** — they share `/ingestion/sam-gov/run`'s rate-limit budget, and the
  limiter's state lives in the API process's memory (doesn't reset between
  pytest invocations).

## Architecture

**Database**: PostgreSQL. Tenant-owned tables (`products`, `opportunities`,
`product_capabilities`, `audit_log`) are isolated by Row-Level Security at
the connection level — not by application code remembering a `WHERE
tenant_id = ...` clause. Every route that touches tenant data must call
`set_config('app.current_tenant', ...)` on the session first (done centrally
by the `get_tenant_session` dependency in `api/app/main.py`); RLS then
enforces the boundary even against a buggy or malicious query.

Shared reference tables (`programmes`, `organizations`, `markets`,
`capability_taxonomy`, `sources`, `evidence`) have **no** tenant scoping by
design — every tenant reads the same ingested market/programme intelligence.

Two DB roles: the running API connects as `doi_app` (no `CREATE` privilege —
real least-privilege, not just RLS); Alembic migrations run as a separate,
more-privileged role (`ALEMBIC_DATABASE_URL`, `postgres` locally). Don't
"fix" a migration permission error by widening `doi_app`'s grants.

**No ORM.** All queries are raw SQL via SQLAlchemy Core `text()`. There are
no declarative models, so `alembic revision --autogenerate` will not work —
every migration is hand-written, and `db/migrations/*.sql` remain the actual
source of truth (each Alembic revision is a thin wrapper that reads and
executes the corresponding `.sql` file).

**Evidence model**: nothing may claim a fact without a traceable source.
Every classification, ingested programme, and match carries `source_id`,
`confidence` (`high`/`medium`/`low`), and `evidence_status`
(`verified`/`reported`/`estimated`/`ai_inferred`/`unverified`) — enforced by
schema, not just convention.

**Human-in-the-loop gate**: AI-suggested capability classifications
(`classified_by = 'ai_suggested'`, from `POST /products/{id}/classify`) are
never used for programme matching until an analyst explicitly confirms them
(`classified_by = 'analyst'`, via `POST /products/{id}/capabilities/{cap_id}/confirm`).
This is enforced in the matching query itself (`app/programme_matching.py`),
not just described as a workflow — don't relax that filter.

**Platform admin vs tenant admin**: `require_role("admin")` is tenant-scoped
(every signup's first user is their own company's admin). `is_platform_admin`
on `users` is a genuinely separate flag, checked fresh from the DB on every
write (never trusted from the JWT), gating writes to shared data like
`capability_taxonomy`. No signup path can set it — only a manual `UPDATE
users SET is_platform_admin = true ...`. Never conflate the two when adding
a new route that touches shared reference data.

### Ingestion registry (`api/app/ingestion_common.py`, `INGESTION_SOURCES` in `main.py`)

Adding a new procurement data source means writing a `run_<source>_ingestion`
module and appending one `IngestionSourceConfig` entry to `INGESTION_SOURCES`
in `api/app/main.py` — not touching the scheduler, status endpoints, or error
handling. `run_scheduled_source` is the one generic wrapper every source gets
for free (quiet skip on missing config via `IngestionConfigError`, logged
failure otherwise). Current sources: `sam_gov` (needs `SAM_GOV_API_KEY`,
NAICS-code rotation via `ingestion_rotation_state`), `uk_find_a_tender` (no
key, client-side CPV filtering, date-range pull), `ted_eu` (no key).

Rotation (`get_rotation_index`/`advance_rotation_index`) is per-source via an
explicit `rotation_key` string — only advances after a real attempt
genuinely completes, so a config error never silently skips a group.

### Programme matching (`api/app/programme_matching.py`, `api/app/matching_scoring.py`)

Matches a product's **confirmed** capabilities to programmes via two unioned
signals: `taxonomy_naics_mapping` (US) and `taxonomy_cpv_mapping` (UK) — both
curated v1 starting sets, not exhaustive — plus a keyword re-score of the
programme's own title. A classification-code-only match with no keyword
corroboration is capped at "low confidence" (fixed after a real false-positive
was found in live SAM.gov data — e.g. category-only matches producing
nonsense results). The API response field is `naics_match` for backward
compatibility even though it now means "matched via any classification code,
NAICS or CPV"; `matched_classification_code` is the newer, honestly-named
field.

Known naming debt: `programmes.naics_code` stores CPV codes for non-US
sources too. Don't assume the column name reflects its content for every row.

### Auth

JWT (`sub`/`tenant_id`/`role`/`exp` only — no human-readable fields, hence
`GET /auth/me`), Argon2 password hashing. `get_tenant_session` is the
dependency that both authenticates and sets the RLS tenant context in one
step; almost every tenant-scoped route depends on it rather than
`get_current_user` alone.

### Rate limiting & CORS

`slowapi`, keyed by client IP, global default `300/minute` plus tighter
per-route overrides for signup/login/SAM.gov-triggering (all env-configurable:
`DEFAULT_RATE_LIMIT`, `SIGNUP_RATE_LIMIT`, `LOGIN_RATE_LIMIT`,
`INGESTION_RATE_LIMIT`). CORS is a real allow-list (`FRONTEND_ORIGIN`) — note
it must be set in **both** `.env` and referenced in `docker-compose.yml`'s
`api.environment` block; Compose does not inject a `.env` var into a
container unless the service's `environment:` section explicitly names it.

## Known limitations worth remembering while working here

- The scheduler (APScheduler) only runs while the API process is up — no
  always-on deployment exists yet, so "scheduled ingestion" is real in code
  but not continuously active outside a running `docker compose` session.
- No eligibility/export-control tagging exists yet — every ingested
  opportunity is shown to every tenant with no legal eligibility filter. Real
  legal review is required before this ships to any real customer; don't
  treat it as a simple feature add.
- Cross-tenant Partner/JV matching and monetization are designed (see
  README's roadmap) but not built — no code exists for them yet.
