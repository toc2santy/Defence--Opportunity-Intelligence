# Defence Opportunity Intelligence — Engineering Status

This document reflects what's actually built and verified, not the
original aspirational brief. Every claim below has been run against
a real database, a real API, and — where noted — a real external
government data source. Where something is a known gap or
limitation, it's stated plainly rather than glossed over; that
discipline is what makes the rest of this document trustworthy.

## At a Glance — What's Actually Done

| Area | Status |
|---|---|
| Multi-tenant foundation (schema, auth, RBAC, RLS, audit log) | ✅ Built, tested |
| Capability Intelligence (classification, human-in-the-loop) | ✅ Built, tested |
| Market/Programme data — SAM.gov (US) | ✅ Built, live-tested, real data ingested |
| Market/Programme data — UK Find a Tender | ✅ Built, tested |
| Programme matching (NAICS + CPV, keyword-corroborated) | ✅ Built, tested, self-corrected twice on real evidence |
| Generalized multi-source ingestion registry | ✅ Built, tested — proven with 2 real, architecturally different sources |
| Scheduled ingestion + "what's new" (early-visibility mechanism) | ✅ Built, tested |
| Automatic NAICS rotation (real coverage widening over time) | ✅ Built, tested live |
| Platform Admin + Taxonomy growth UI | ✅ Built, tested live |
| Alembic migrations (least-privilege by design) | ✅ Built, tested |
| Secrets management (`.env`, git-ignored) | ✅ Built, tested |
| Rate limiting (real, tested, not decorative) | ✅ Built, tested |
| Real frontend (auth, My Products, classify/confirm/match) | ✅ Built, working live |
| **India market coverage** | ❌ No public API found — licensed-data/manual-entry decision, not an engineering task |
| **Customer/OEM/Competitor Intelligence** | ❌ Deliberately deferred — reasoned through explicitly, not forgotten |
| **Cross-tenant Partner/JV Matching** | 🟡 Designed, not built — see roadmap below |
| **Monetization / billing** | 🟡 Designed, not built — see roadmap below |
| **Eligibility/export-control tagging** | ❌ Not built — real legal review required first |
| **Production deployment** | ❌ Deliberately not done yet — correct call until there's a real reason to go live |

95 automated tests, 92 passing / 3 intentionally skipped (live
external-API tests, opt-in by design to avoid hammering real
government services on every routine run).

## Path to a Complete Enterprise Version

Organized by what's actually blocking a real launch, not by how
interesting each item is to build.

### 1. Legal & compliance review — the real gating item, not engineering

Nothing below should ship to a real customer before a qualified
professional reviews it. This is stated as plainly as every other
limitation in this document because it's the single most consequential
one:

- **Bidder eligibility / export control tagging.** The platform
  currently shows every ingested opportunity to every tenant with no
  indication of who's actually legally allowed to respond. A US
  defense tender restricted to US persons, shown to an Indian
  company with no eligibility flag, isn't just incomplete — it risks
  wasting a real company's time on something they were never
  eligible for, or worse, touches ITAR-adjacent territory. Needs a
  real classification field per programme (source data often
  already has set-aside/eligibility fields we're not yet capturing)
  and expert review of the labeling logic before it's trusted.
- **Contingent-fee structure for the monetization model.** A fee
  that's a percentage of a *government contract's* value, contingent
  on winning it, runs into real restrictions in multiple
  jurisdictions (e.g. the US FAR "Covenant Against Contingent
  Fees"). The lower-risk version — an introduction/matchmaking fee
  charged on mutual agreement, priced off a programme's *publicly
  disclosed* estimated value rather than a private contract-award
  amount — still needs sign-off before implementation, not just
  before launch.
- **Data licensing terms for every new source.** SAM.gov and UK Find
  a Tender were both verified as genuinely open/public before
  building against them. Every future source needs the same check,
  not an assumption it'll be the same.

### 2. Cross-tenant Partner/JV Matching (Phase 4)

Designed in conversation, not yet built. Real open decisions, made
explicit rather than assumed:

- **Trigger**: two tenants whose confirmed capabilities both matched
  the *same real programme* (buildable from data already in the
  `opportunities` table — no new ingestion needed).
- **Visibility model**: mutual opt-in reveal — a match is shown
  anonymized ("a potential partner exists for Opportunity X") until
  *both* sides accept, only then is identity/contact info exchanged.
  Chosen over immediate full-reveal specifically to avoid exposing a
  company to a competitor without their own consent.
- **Discoverability**: default-**off** per tenant — a company has to
  deliberately opt in to being matchable at all, consistent with
  this platform's existing "invisible by default" tenant-isolation
  posture.
- **Real architectural work required**: a new table that
  legitimately spans two tenants (unlike every other table in this
  schema, which is single-tenant-scoped by RLS), and
  application-layer redaction before mutual consent — RLS controls
  which *rows* are visible, not which *columns* get hidden pre-consent.

### 3. Monetization (once #1's fee-structure question is cleared)

- **Tier 1 — subscription gate**: partner-matching as a
  Professional/Enterprise-only feature. Lowest legal risk, buildable
  immediately once Phase 4 itself exists.
- **Tier 2 — introduction fee**: charged to both parties on mutual
  match acceptance, priced off public estimated contract value.
  Standard matchmaking-commission shape, not contract-contingent.
- **Tier 3 — success fee tied to actual contract value**: real
  revenue upside, real legal risk (see #1) — **do not build until
  cleared**. `USASpending.gov` (already researched, confirmed free
  and public) becomes relevant again here specifically as a
  billing-verification tool — confirming a real contract win
  independent of self-reporting — not as a standalone feature.
- **Non-negotiable integrity rule, stated now so it's not
  renegotiated later under revenue pressure**: the matching/
  confidence algorithm is never tuned based on revenue outcomes,
  only on classification accuracy — same discipline as every
  self-correction already made in this project (the false-positive
  fix from real SAM.gov data being the clearest precedent).

### 4. Data quality & coverage

- NAICS-to-taxonomy and CPV-to-taxonomy mappings are both honest
  **v1 starting sets**, not finished, domain-expert-reviewed
  taxonomies — see the "Known limitations" section below for exactly
  which capabilities are and aren't mapped, and why.
- `programmes.naics_code` holds CPV codes for UK-sourced data too —
  a real naming debt, harmless functionally, worth fixing before a
  third source arrives and the column name gets even less accurate.
- Additional real sources (EU TED, others) remain unresearched —
  each needs the same verification pass SAM.gov and UK Find a
  Tender got before any code is written against them.
- India coverage requires a business decision (licensed data
  partnership or a resourced manual-analyst-entry workflow using
  the schema's existing `customer_provided` source type), not
  further engineering.

### 5. Production readiness (deliberately not started)

Correctly deferred until there's a real reason to go live — see
"Running it locally" below for the current all-local setup. When
that time comes: managed database with real backups, secrets out of
local `.env` into a real secrets manager, an actually-always-on
deployment (the scheduler only runs while the API process does),
and the Postgres passwords currently hardcoded for local-only use
finally rotated to something deployment-grade.

## What's real vs. what's still aspirational

The original product brief described ten intelligence engines. As
of this writing:

| Engine | Status |
|---|---|
| Capability Intelligence | **Real.** Weighted keyword taxonomy, human-confirmed, evidence-backed. |
| Market Intelligence | **Partial.** Real for U.S. federal (SAM.gov). India (SRIJAN/iDEX) has no public API — unsolved, see below. |
| Programme Intelligence | **Real.** NAICS + keyword matching, self-corrected once against real false positives. |
| Customer Intelligence | Not started (Phase 4). |
| OEM & Partner Matching | Not started (Phase 4). |
| Competitor Intelligence | Not started (Phase 4). |
| Opportunity Intelligence | **Real**, but only as the direct output of Programme Intelligence matching — no independent scoring model beyond that yet. |
| Procurement Intelligence | **Real**, folded into the SAM.gov ingestion (opportunity stage, deadlines). |
| Engagement Intelligence | Not started. |
| Next-Best-Action | Not started. |

The original marketing site prototype's "engine" logic (an 11-line
keyword table, hash-based fake scores) has been fully superseded by
the code below wherever a phase covers the same ground. Where a
phase hasn't been reached yet, the prototype UI is still
disconnected from anything real — don't mistake it for coverage.

## Architecture

**Database**: PostgreSQL. Tenant-owned data (`products`,
`opportunities`, `product_capabilities`, `audit_log`) is isolated
via Row-Level Security — enforced at the database connection level,
not by application code remembering a `WHERE tenant_id = ...`
clause. Shared reference data (`programmes`, `organizations`,
`markets`, `capability_taxonomy`, `sources`, `evidence`) has no
tenant scoping by design — every tenant benefits from the same
ingested market/programme intelligence.

**API**: FastAPI, JWT auth, Argon2 password hashing. Every route
that touches tenant data sets the Postgres session's tenant context
before querying, which RLS then enforces.

**Evidence model**: nothing in this system claims a fact without a
traceable source. Every classification, every ingested programme,
every match carries a `source_id`, a `confidence` level
(`high`/`medium`/`low`), and an `evidence_status`
(`verified`/`reported`/`estimated`/`ai_inferred`/`unverified`).
This is enforced by schema, not just UI copy.

**Human-in-the-loop**: AI-suggested capability classifications
(`classified_by = 'ai_suggested'`) are never used for programme
matching until an analyst explicitly confirms them
(`classified_by = 'analyst'`). This gate is enforced in the
matching query itself, not just described as a workflow.

## What's actually built, phase by phase

### Phase 0 — Foundation
17-table schema, tenancy, RBAC, audit logging, source/evidence
framework. Signup/login with real password hashing. Verified with
a full automated test suite, not just manual `curl` checks.

### Phase 1 — Capability Intelligence
`POST /products/{id}/classify` — weighted keyword matching against
an extensible `capability_taxonomy` (20 sectors, ~100 seeded
keywords), replacing the prototype's 11-entry hardcoded lookup.
Every classification stores which keywords matched and at what
score — genuinely explainable, not a black-box label.
`POST /products/{id}/capabilities/{cap_id}/confirm` and
`.../reject` implement the human-in-the-loop gate.

### Phase 2 — Market & Programme Data (SAM.gov)
`POST /ingestion/sam-gov/run` — real, live ingestion from the U.S.
government's public Contract Opportunities API. Verified against
GSA's own documented example responses for the parsing logic, and
against the actual live API for the full round-trip (100 real
federal opportunities ingested and confirmed present in the
database during this project).

**India (SRIJAN/iDEX) has no public API** — confirmed by direct
research, not assumed. This is not an engineering gap to "fix
later" — it's a fundamentally different problem (licensed data
partnership or manual analyst entry via the schema's existing
`customer_provided`/`internal_analyst` source types) that needs a
business decision, not more code.

**SAM.gov API key expiry is tracked in the database**, not just
remembered by a person. `GET /ingestion/sam-gov/key-status` reports
days remaining; every `/ingestion/sam-gov/run` response includes
the current status automatically. `PATCH /ingestion/sam-gov/key-status`
updates it after a key rotation. Personal-tier keys are rate
limited to roughly 10 requests/day — ingestion defaults to 3 NAICS
codes per run to stay well under that.

### Phase 3 — Programme Matching
`POST /products/{id}/match-programmes` — connects a product's
CONFIRMED capabilities to real ingested programmes via two combined
signals: a curated NAICS-to-taxonomy mapping, and a keyword
re-score of the programme's own title. This is the first code in
the project that populates the `opportunities` table, which existed
in the schema since Phase 0 but was never written to until now.

**Self-corrected once already, on real evidence**: the first live
run surfaced genuine false positives (programmes sharing a NAICS
code with a product but zero actual textual relevance — e.g. "GPS
HATCH SYSTEMS" matching a radar product on category alone). The
scoring logic was fixed so a NAICS-only match with no keyword
corroboration can never be labeled better than "low confidence" —
a real finding from real data, not a hypothetical worth worrying
about later.

### Scheduled Ingestion + "What's New" (the real "early achiever" mechanism)

A deliberate detour from the numbered roadmap, prompted by a
grounding question worth recording: does knowing *who won* a
government contract actually help a defense SME sell their product?
Honest answer: not much, on its own — and claiming "you won because
of us" would be a causally overreaching claim this platform has no
basis to make. What genuinely *is* provable, and actually useful, is
**visibility timing**: did you find out about a real opportunity
earlier than you otherwise would have?

That's what this piece implements, concretely:

- **`scheduled_sam_gov_ingestion`** runs automatically on a timer
  (`SCHEDULED_INGESTION_INTERVAL_HOURS`, default 24) via APScheduler,
  registered on API startup — ingestion no longer depends on anyone
  remembering to trigger it manually.
- **`GET /opportunities/new`** — returns only opportunities created
  since the tenant last checked, then advances a server-side
  checkpoint (`tenants.opportunities_last_viewed_at`). The tenant
  never writes to that checkpoint directly, which is what makes "you
  saw this first" a provable timestamp rather than a marketing line.
  `?mark_seen=false` allows peeking without advancing it.
- **`GET /ingestion/scheduler-status`** — confirms the job is
  genuinely registered and shows its next run time.

Customer/OEM/Competitor Intelligence (the original roadmap's Phase 4)
was deliberately deferred in favor of this — reasoned through
explicitly rather than just following the numbered plan, on the
grounds that it doesn't answer a real user's actual daily question
("what should I act on today") the way this does.

## Running it locally

```bash
cp .env.example .env
# fill in JWT_SECRET (generate with: python3 -c "import secrets; print(secrets.token_urlsafe(48))")
# leave SAM_GOV_API_KEY blank unless you're running live ingestion

docker compose up --build
# API on http://localhost:8000, Postgres on localhost:5432

# apply migrations in order, against a fresh or existing DB:
docker compose exec -T db psql -U postgres -d doi < db/migrations/002_capability_keywords.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/003_ingestion_sam_gov.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/004_programme_matching.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/005_api_credentials.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/006_scheduled_ingestion_and_notifications.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/007_platform_admin.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/008_naics_rotation.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/009_uk_find_a_tender.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/010_taxonomy_cpv_mapping.sql
docker compose exec -T db psql -U postgres -d doi < db/migrations/011_product_deletion_cascade.sql
```

To actually ingest real data, get a free SAM.gov Public API Key
(SAM.gov → Account Details) and set `SAM_GOV_API_KEY` before
starting Docker.

## Migrations

Managed with Alembic (`api/alembic/`). Each revision is a thin
wrapper that reads and executes the corresponding raw `.sql` file
under `db/migrations/` — those files remain the actual source of
truth for what each migration does; Alembic adds version tracking,
`upgrade`/`downgrade` orchestration, and a real `alembic_version`
table, instead of everyone remembering by hand which numbered
`.sql` file they last ran manually.

**On a database that already has this schema applied** (true for
this project's own dev database, since the six migrations were
originally applied by hand before Alembic was adopted) — tell
Alembic the current state without re-running anything. Run this
inside the already-running API container, not from your host shell
— it already has Alembic installed and the correct `DATABASE_URL`
for the Docker network, so there's nothing extra to configure:

```bash
docker compose exec api alembic stamp 0006_scheduled_ingestion
```

**On a genuinely fresh, empty database** — run everything from
scratch:

```bash
docker compose exec api alembic upgrade head
```

**Going forward, once there's an actual schema change to make**:

```bash
docker compose exec api alembic revision -m "add competitor_awards table"
# hand-write the upgrade()/downgrade() in the new file under alembic/versions/
# (it'll appear in ./api/alembic/versions/ on your host too, since
# the api/ folder is not copied-once but part of your normal project files)
docker compose exec api alembic upgrade head
```

**No autogenerate** (`alembic revision --autogenerate`) — this
project has no declarative SQLAlchemy ORM models to diff against
(queries are raw SQL via `text()`), so every migration is hand-
written, same as the raw `.sql` files it replaces. This is a
deliberate, honest limitation stated once here rather than
discovered by surprise later.

**Migrations run under a different, more privileged database role
than the app itself** (`ALEMBIC_DATABASE_URL`, defaulting to
`postgres` in local dev — see `docker-compose.yml` and
`alembic/env.py`). The running API connects as `doi_app`, a role
deliberately granted no `CREATE` privilege on the schema — real
least-privilege, not just RLS. Migrations genuinely need
schema-altering rights, so rather than weaken `doi_app`'s grants to
get there, they run under a separate credential instead. This was
discovered, not designed upfront in the abstract: the first real
`alembic stamp` attempt failed with a permissions error trying to
create the `alembic_version` table as `doi_app` — exactly the
least-privilege boundary working as intended, not a bug.

**Downgrades are honest, not decorative.** Several `downgrade()`
functions deliberately do less than a perfect mirror of their
`upgrade()` — e.g. `0003` and `0004` leave `programmes.naics_code`
and the seeded SAM.gov source row in place even on downgrade,
because by the time anyone runs one, real ingested programme data
depends on them, and silently stripping that out would be a data
loss bug hiding inside a "revert this migration" command. Each one
says exactly what it does and doesn't undo, in a comment, rather
than pretending symmetry it doesn't have.

## Testing

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

53 tests, 1 intentionally skipped (the live SAM.gov integration
test, which only runs when `SAM_GOV_API_KEY` is set — everything
else runs without any external credentials or network access).

Split deliberately into pure unit tests (no DB, no network — the
scoring/normalization/matching *logic* itself) and integration
tests (hit the real running stack over HTTP, no mocking). This
split has already caught bugs the other kind wouldn't have: DB
permission grants, a jsonb serialization quirk, a SQLAlchemy `::`
cast parsing gotcha, and a UUID type mismatch in an audit log call
— none of which a mocked-DB test would have surfaced, because they
only appeared when real SQL hit a real Postgres instance.

## Product Deletion — Closing a Real Gap

Found by a straightforward, honest question: "how do I fix it if I
entered wrong data?" There was no way. `DELETE /products/{id}` now
exists, tenant-scoped by RLS the same way every other route is —
attempting to delete another tenant's product returns 404, not 403,
so it doesn't even confirm a product with that ID exists elsewhere.

**A real bug this surfaced and fixed**: `product_capabilities` was
already set to cascade-delete correctly when its product is
removed, but `opportunities` was never given the same treatment —
meaning deleting a product that already had a real matched
opportunity would have failed outright with a foreign key
violation. `db/migrations/011_product_deletion_cascade.sql` fixes
this, and `tests/test_product_deletion.py` includes a test that
specifically creates a real matched opportunity first, then deletes
the product, to prove the fix actually works rather than just
trusting the migration ran.

## CPV-to-Taxonomy Mapping (Closing the UK Matching Gap)

The gap flagged honestly when UK Find a Tender was built — real UK
data ingested, but invisible to Phase 3 matching since
`taxonomy_naics_mapping` only knew NAICS codes — is closed.
`taxonomy_cpv_mapping` follows the exact same structure, and
`app/programme_matching.py` now unions both mapping tables before
querying candidate programmes, so a tenant's confirmed capability
matches against NAICS-coded (US) and CPV-coded (UK) programmes
alike, transparently.

**Same honesty standard as the NAICS mapping — not every capability
got a CPV entry.** Several, including `UAV.INTEGRATION` — this
whole project's own flagship example — are deliberately left
unmapped, because no verified, defensible UAV-specific CPV code was
found. `tests/test_cpv_mapping.py` includes a test that specifically
asserts this gap still exists, so it fails loudly (as a prompt to
update the test, not a bug) the day someone actually adds a real
mapping for it, rather than silently describing a gap that's since
been closed.

**Internal renaming, no external API contract change.**
`matching_scoring.py`'s `NAICS_MATCH_BONUS`/`naics_match` became
`CATEGORY_MATCH_BONUS`/`category_match` — purely internal Python
names, verified behavior-identical before and after (same scores,
same confidence labels, checked directly). The
`/products/{id}/match-programmes` response still returns
`naics_match` for backward compatibility with the existing frontend
and tests; it now honestly means "matched via any classification
code, NAICS or CPV," documented in `programme_matching.py` rather
than silently redefined. A new additive field,
`matched_classification_code`, shows which code actually matched.

## UK Find a Tender Service — Second Real Source

The proof that the generalized registry actually works, not just a
description of it: `INGESTION_SOURCES` in `main.py` now has two
entries, and adding the second one required no scheduler change, no
new status endpoint, no duplicated error handling — exactly the
point of Step 1.

**Genuinely different from SAM.gov in ways worth naming, not
glossed over:**

- **No API key at all** — confirmed from GOV.UK's own API
  documentation, which shows no authentication for the
  `ocdsReleasePackages` read endpoint (a submission/publish API
  exists separately and does require OAuth2, but that's not what
  this platform uses).
- **No server-side category filter.** Unlike SAM.gov's `ncode`
  parameter, Find a Tender's API has no way to ask for "only CPV
  35xxxxxx" — every call returns whatever's in the date/stage
  window regardless of category, and defense-relevance is filtered
  **client-side** in `app/uk_ft_normalize.py` after fetching.
- **No NAICS-style rotation needed.** Rotation existed for SAM.gov
  specifically because of its per-code server-side fetching; a
  date-range pull here naturally covers every category in one call.
- **No fixed daily quota is documented** — the API enforces its own
  dynamic rate limit (real `429` responses include a `Retry-After`
  header, which the connector surfaces rather than hiding).

**CPV codes verified against multiple independent procurement-
classification sources**, not guessed: the entire `35` division
("Security, fire-fighting, police and defence equipment") plus four
specific `50`-series codes for military repair/maintenance services
that sit outside that division. This is a v1 starting set, same
honesty as the NAICS-to-taxonomy mapping — real, sourced, not
exhaustive.

`app/uk_ft_normalize.py` is tested against GOV.UK's own documented
example response (17 unit tests, all independently verified against
the real schema before being committed — including confirming the
official example itself, being agricultural procurement, correctly
gets filtered OUT as non-defense-relevant). The live round-trip is
tested via `POST /ingestion/uk-ft/run`, gated behind
`RUN_LIVE_UK_TEST=1` — deliberately opt-in even though no credential
gates it, out of the same respect for not hammering a live
government service on every routine test run that motivates
SAM.gov's key-based gate.

**Known naming debt, stated plainly**: UK programme records store
their CPV code in the `programmes.naics_code` column — a genuinely
misleading column name for a non-US source. Fixing this properly
means either renaming the column to something country-agnostic
(`classification_code`) or adding a parallel `classification_scheme`
column, either of which is a real migration worth doing before a
third source arrives, not a cosmetic nice-to-have.

**Also not yet built**: UK-sourced programmes won't show up in
Phase 3 programme matching yet, since `taxonomy_naics_mapping` only
maps capability categories to NAICS codes, not CPV codes. Real,
ingested, evidence-backed UK data is visible in the Opportunity
dashboard today; matching it to a tenant's classified capabilities
needs a `taxonomy_cpv_mapping` table (or a generalized version of
the existing one) as a follow-up step, not assumed to work already.

## Generalized Ingestion Registry

Refactored ahead of adding a real second data source (UK Contracts
Finder, confirmed to have a genuine free public API — see "Broader
Vision" below), rather than after — adding a second source to code
that was hardcoded around SAM.gov specifically would have meant
duplicating scheduling, error-handling, and status-reporting logic,
not reusing it.

`app/ingestion_common.py` now holds the source-agnostic pieces:
`IngestionSourceConfig` (a small dataclass any source registers
itself with), `run_scheduled_source` (one generic scheduled-run
wrapper — quiet skip on missing config, logged failure otherwise,
same for every source), and generalized rotation helpers
(`get_rotation_index`/`advance_rotation_index`, now taking an
explicit `rotation_key` instead of a hardcoded SAM.gov-specific
string, so a future source can have its own rotation cursor without
colliding with another's).

**Proven by refactoring the existing SAM.gov ingestion to go
through this — not by designing the abstraction in the abstract for
a hypothetical future source.** `INGESTION_SOURCES` in `main.py` is
currently a list of one entry; adding UK Contracts Finder means
appending a second entry, not writing parallel infrastructure.
`GET /ingestion/sources/status` is the genuinely generalized status
view (works across every registered source); the original
`GET /ingestion/scheduler-status` stays exactly as it was for
backward compatibility with existing tests and any code depending
on its response shape.

## NAICS Rotation (Automatic Coverage Widening)

A real gap, found by asking a direct question rather than assumed
fixed: the scheduled daily ingestion had been running (whenever
`SAM_GOV_API_KEY` is set) but always pulling the exact same first 3
NAICS codes every single time — `run_sam_gov_ingestion`'s old
default was a static slice, not a rotating one, so successive runs
never actually widened real coverage, they just re-confirmed the
same narrow slice was still current.

Fixed with a real persisted rotation cursor
(`ingestion_rotation_state` table). When `naics_codes` is omitted —
true both for the scheduler AND for a manual
`POST /ingestion/sam-gov/run` call with no explicit codes — the
system automatically uses the next group in rotation and advances
the cursor, only after a real attempt genuinely completes (a
missing API key or config error never advances it, so no group gets
silently skipped). Passing `naics_codes` explicitly bypasses
rotation entirely and doesn't touch the cursor, so an admin
choosing specific codes for a one-off pull doesn't disrupt the
scheduled rotation's progress.

`GET /ingestion/scheduler-status` now reports real rotation state —
current index, total groups, and exactly which codes the next run
will use — not just whether a timer is registered.

`tests/test_naics_rotation_unit.py` verifies the pure rotation math
(wrapping, uneven splits, edge cases). `tests/test_naics_rotation_live.py`
proves it genuinely advances through the real live endpoint —
skipped without a real key, same pattern as the other live test,
and sharing that same test's warning about not running multiple
`/ingestion/sam-gov/run`-consuming test files together in one sweep.

## Capability Taxonomy Growth (Platform Admin)

A real, deliberately-caught security fix, not a feature built in
isolation. When asked how the classifier could ever cover a
product that doesn't match its existing ~100 seed keywords, the
honest first answer was: it can't yet, and there's no way to add
new capability categories without editing a SQL migration by hand.
Fixing that raised a real question worth stopping for before
writing any code: **who should be allowed to add to shared,
platform-wide classification data that every tenant's classifier
reads from?**

The existing `admin` role is tenant-scoped — every signed-up user
IS their own company's admin by design (see "Frontend Integration"
below). Gating taxonomy writes behind that role would let ANY
customer's admin edit data that affects EVERY OTHER customer. So
this introduces a genuinely separate `is_platform_admin` flag on
`users`, checked fresh from the database on every write (not
trusted from the JWT, so a grant/revoke takes effect immediately).
**No signup path can set it.** The only way to become a platform
admin is a manual database update:

```bash
docker compose exec db psql -U postgres -d doi -c "update users set is_platform_admin = true where email = 'you@example.com';"
```

Endpoints:
- `GET /admin/taxonomy` — open to any authenticated user (read-only, not sensitive)
- `GET /admin/taxonomy/{id}/keywords` — same
- `POST /admin/taxonomy` — **platform admin only**, creates a new capability category
- `POST /admin/taxonomy/{id}/keywords` — **platform admin only**, adds a weighted keyword
- `DELETE /admin/taxonomy/keywords/{id}` — **platform admin only**

`tests/test_taxonomy_admin.py` includes the test that actually
matters most here: proving a regular, freshly-signed-up tenant
admin gets a real 403 trying to write to shared taxonomy data — not
just that the happy path works for a platform admin.

## Frontend Integration

Two small backend additions, made specifically to unblock wiring a
real frontend to this API instead of the existing SPA prototype's
fake logic:

- **CORS**, restricted to an actual allow-list
  (`FRONTEND_ORIGIN` env var, defaulting to `http://localhost:5500`
  — set it in `.env` AND make sure it's listed in
  `docker-compose.yml`'s `api.environment` block; a real bug during
  this project's own testing was setting it in `.env` alone and
  assuming that was enough — Compose only injects a variable into a
  container if the service's `environment:` section explicitly
  references it, `.env` by itself is just a lookup source)
  — not wide open. A browser calling this API from any other origin
  is correctly blocked; this is enforced by the browser reading the
  response headers, not by the server rejecting the request, which
  is why the test for it checks header presence/absence rather than
  status codes.
- **`GET /auth/me`** — the JWT payload only carries
  `sub`/`tenant_id`/`role`, not anything human-readable. This is
  what a real login flow calls next to get a company name and email
  to actually show someone. Scoped safely by construction: it only
  ever looks up the calling user's own row from their own
  JWT-verified `sub`, never takes an ID as input.

Frontend work itself is intentionally staged, not a rewrite: most of
the existing prototype SPA (Platform, Solutions, Industries, Pricing,
About, Resources) never claimed to show real data and doesn't need
touching. Only the screens that DO claim to show data — the
capability-classification demo and the opportunity dashboard — get
rewired to the real endpoints. The Global Markets tab is left
honestly static rather than wired to fake global coverage, since
real coverage is U.S.-only right now.

## Rate Limiting

Real, enforced, tested — not decorative. Uses `slowapi`, keyed by
client IP. A generous global default (`300/minute`, env-configurable
via `DEFAULT_RATE_LIMIT`) applies to every route automatically;
three routes that genuinely warrant tighter limits are overridden
individually:

| Route | Default limit | Why |
|---|---|---|
| `POST /auth/signup` | `100/hour` | Blocks mass fake-account creation while staying well above this project's own ~25-30 test-suite signups per run |
| `POST /auth/login` | `200/hour` | Blocks brute-force password guessing |
| `POST /ingestion/sam-gov/run` | `5/hour` | No reason to allow more frequent triggering than SAM.gov's own ~10/day personal-key quota supports |

All three are env-configurable (`SIGNUP_RATE_LIMIT`,
`LOGIN_RATE_LIMIT`, `INGESTION_RATE_LIMIT`) so a real deployment can
tighten them without a code change.

`tests/test_rate_limiting.py` proves the mechanism actually returns
a 429 after the real limit is exceeded — deliberately written to
stay correct even if the suite is run more than once within the
same hour, since the limiter's state lives in the API process's
memory and doesn't reset between pytest invocations. See that file's
docstring, and the warning in `test_sam_gov_live.py`, for the one
real interaction this creates: don't run the live SAM.gov test as
part of a full sweep together with the rate-limit test, since they
share the same endpoint's budget by design.

## Known limitations — stated plainly, not hidden

- **India market coverage does not exist.** No public API for
  SRIJAN/iDEX was found. This needs a licensed-data or manual-entry
  decision, not an engineering fix.
- **The NAICS-to-taxonomy mapping is a curated v1 starting set**
  (`db/migrations/004_programme_matching.sql`), not a finished,
  domain-expert-reviewed taxonomy. It will both miss real matches
  and occasionally over-match until someone with real defense
  procurement classification expertise reviews and extends it.
- **SAM.gov is U.S. federal-prime only.** No state, local,
  education, or non-U.S. procurement coverage.
- **The scheduler only runs while the API process is running.** On
  a laptop that's only up during dev sessions, "scheduled" is
  theoretical, not actually happening in the background. This needs
  a genuinely always-on deployment (a real server, not local Docker)
  before the "you saw this first" claim is true in practice, not
  just true in the code.
- **"What's new" tracking is per-tenant, not per-user.** If multiple
  people share one tenant login, one person checking marks it seen
  for everyone. Fine for now; worth revisiting once multi-user
  tenants are a real usage pattern.
- ~~No Alembic migration history~~ — **resolved.** See "Migrations"
  below. The old hand-numbered `db/migrations/*.sql` files are kept
  as-is and still the actual source of truth for what each
  migration does; Alembic revisions wrap them rather than
  duplicating their content, so there's one copy of each migration's
  SQL, not two that could silently drift apart.
- **`JWT_SECRET` is now properly managed via a git-ignored `.env`
  file**, generated randomly rather than a checked-in placeholder —
  this was the most security-sensitive secret in the whole system,
  since leaking it lets anyone forge a valid login token for any
  tenant without ever touching the database. **The Postgres
  passwords (`doi_app`, `postgres` superuser) are still hardcoded**
  in `db/roles.sql` and `docker-compose.yml` — acceptable for now
  because the database port is only reachable on localhost in this
  local-only setup, but this genuinely must be fixed before any real
  deployment where that port might be reachable from anywhere else.
- **Customer/OEM/Competitor Intelligence (the original roadmap's
  Phase 4) is deliberately deferred**, not forgotten — reasoned
  through explicitly (see above) rather than built reflexively
  because it was next in a numbered list.

## Why RLS instead of just filtering in application code

A `WHERE tenant_id = :tenant_id` clause in every query works right
up until one route forgets it — and in a codebase with dozens of
routes, that's a `when`, not an `if`. Postgres RLS makes the tenant
boundary a property of the database connection itself: even a buggy
or malicious query literally cannot return another tenant's rows,
because Postgres filters them out before the application ever sees
them. This has been independently verified twice — once by hand
(a second tenant genuinely could not see a first tenant's product)
and once automatically (the test suite asserts it on every run).
