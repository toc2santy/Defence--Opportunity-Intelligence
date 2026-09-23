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

**Caveat, found live at migration 0045 (MFA, 2026-09-23)**: `alembic
upgrade head` cannot run any migration whose .sql file has more than
2 top-level statements — a real asyncpg/SQLAlchemy async-dialect gap,
not a bug in the migration's SQL itself (see the MFA entry below for
the full detail). If a fresh-DB `alembic upgrade head` run fails on
a migration like that, apply it directly instead and stamp past it:
```bash
docker compose exec -T db psql -U postgres -d doi < db/migrations/0NN_name.sql
docker compose exec api alembic stamp 0NN_name
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

Requires the **isolated test stack** running, not the real dev stack —
see below. Tests hit its API over HTTP and, for setup only, its DB
directly as `postgres` (bypasses RLS, safe since setup never asserts
through that connection). No mocking. Tests are split:

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

### Isolated test stack (`docker-compose.test.yml`, 2026-09)

`tests/conftest.py` defaults to a SEPARATE stack (API on 8001, DB on
5433), not the real dev stack this file's "Running it locally"
section starts. A real, expensive lesson from this session's own
history is why: every earlier test run signed real throwaway tenants
into the SAME database the dev frontend/API were actually using, and
two separate cleanups (2026-09) had to delete 8155 and then 1383 of
them by hand before anyone noticed how large the real `tenants` table
had grown from nothing but test-suite noise.

```bash
docker compose -f docker-compose.test.yml up -d --build
for f in $(ls db/migrations/*.sql | sort); do
  docker compose -f docker-compose.test.yml exec -T db-test psql -U postgres -d doi -f - < "$f"
done
docker compose -f docker-compose.test.yml exec api-test alembic stamp head
```

**Why not just `alembic upgrade head`, the way "Running it locally"
above documents for a fresh DB**: a real, separate, pre-existing gap
found while building this — confirmed live that `alembic upgrade
head` fails immediately on `0001_baseline` itself (not just migration
`0045_mfa`, see that migration's own entry further down this file for
the fuller technical explanation) with `cannot insert multiple
commands into a prepared statement`. Every migration in this project
with more than 2 top-level SQL statements in its `.sql` file hits the
same asyncpg/SQLAlchemy async-dialect limitation — roughly a third of
all 45 migrations, `0001_baseline` included, so this was never just a
`0045`-specific issue; it had just never been exercised on a truly
empty database before, since this project's own dev DB has been
continuously stamped-forward since `0001` rather than bootstrapped
fresh. Applying every `db/migrations/*.sql` file directly with `psql`
(which uses Postgres's simple query protocol natively and has no such
restriction — the same fact already used to fix `0045` on the real
DB by hand) then stamping `head` sidesteps the whole class of bug in
one step, for every migration, not just the ones already known to
trip it.

Once running, either export `TEST_API_BASE_URL=http://localhost:8001`
and `TEST_DB_DSN=postgresql://postgres:postgres@localhost:5433/doi`
or just rely on `conftest.py`'s own matching defaults, then run
`pytest` as usual from `api/`.

**5 tests still fail against a genuinely bare stack, and this is
expected, not a bug**: `test_colombia_trigger.py::
test_ingested_colombian_programmes_carry_unspsc_and_a_link`,
`test_cpv_mapping.py::test_uav_product_now_reaches_cpv_coded_award_data`,
`test_product_intel_report.py::
test_competitor_rows_carry_the_evidence_behind_the_count`,
`test_tactical_ppe_capability.py::
test_product_classifies_and_matches_with_high_confidence`, and
`test_taxonomy_admin.py::test_list_taxonomy_carries_real_contribution_counts`
each assert on REAL ingested government tender/award data (real
CPV-coded awards, real Colombia programmes, real classified
opportunities) that only exists after actually running live ingestion
against a source's real external API — something this throwaway,
freshly-migrated stack has never done. 577 of 582 non-skipped tests
pass on a completely bare stack with zero real external data, which
is the honest, current number — not "582 of 582," which would
overclaim what a fresh install actually proves.

MFA tests need `MFA_ENCRYPTION_KEY` set for `api-test` too (a
dedicated throwaway key, committed in this compose file since nothing
this stack stores ever needs to survive between runs — unlike the
real deployment's own key in `.env`, which protects real, persisted
secrets). `test_rate_limiting.py` needs `INGESTION_RATE_LIMIT` to
stay at its real low default (5/hour) — deliberately NOT overridden
here the way `SIGNUP_RATE_LIMIT`/`LOGIN_RATE_LIMIT`/
`PASSWORD_RESET_RATE_LIMIT` are (those need headroom since hundreds
of tests sign up a fresh tenant each), since that test's whole point
is proving a real request eventually gets a 429.

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
key, client-side CPV filtering, date-range pull), `ted_eu` (no key),
`cppp_india` (no key — see below), `canada_buys` (no key),
`south_africa` (no key), `colombia` (no key; an optional
`SOCRATA_APP_TOKEN` only raises the rate limit) and `prozorro` (no
key — see below, the eighth source and architecturally the most
different of all of them).

`canada_buys` is the cleanest source here and the one to copy when
adding another: an open dataset under the Open Government Licence
(no permission question), no key, no pagination, no rate limiting —
one HTTPS GET returns every currently-open tender as CSV (~976 rows,
~84% carrying UNSPSC codes) in about 15 seconds. Two non-obvious
traps, both of which fail as *zero rows* rather than an error:
`httpx`'s default User-Agent gets a **403** from the CDN (send an
explicit UA), and the feed ships a BOM so it must be decoded
`utf-8-sig`. Use `openTenderNotice-*.csv` (all open tenders), **not**
`newTenderNotice-*.csv` — the latter is a rolling 2-hourly delta that
returned as few as 5 rows in testing. Relevance is decided by the
buying entity (DND, CAF, RCN/RCAF, Coast Guard, DRDC) and by
`W`-prefixed PSPC solicitation numbers, *not* by UNSPSC segment —
verified live, defence tenders are spread across many segments.

`south_africa` is the sixth source and the first covering Africa,
researched and verified live before building (see
`db/migrations/020_south_africa_source.sql`). Middle East sources
(Saudi Etimad, UAE eSupply/DGS, Israel mr.gov.il, Jordan/Egypt/Kuwait
portals) were assessed alongside it and rejected — none publish an
open bulk API or dataset; what's reachable there is only paid
third-party aggregators, not a primary government source, the same
class of problem already rejected for GeM. South Africa's own
`ocds-api.etenders.gov.za` is a genuine open government JSON API, no
key required, same OCDS release shape and `links.next` pagination as
UK Find a Tender — but it carries no CPV/NAICS/UNSPSC-equivalent code
(`tender.category` is free text), so like CPPP it decides
defence-relevance from the buying/procuring entity. Confirmed live:
**ARMSCOR** (the Armaments Corporation of South Africa, the state's
defence acquisition agency) appears as a real buyer with genuine
solicitation numbers, deadlines and a named procurement contact.
Matched rows carry the sentinel code `ZA-DEF`. Volume is modest — 1-2
defence-tagged releases per ~100 general releases in initial
sampling, comparable to CPPP's order of magnitude.

**This source was silently dead from 2026-08-24 to 2026-09-15** —
every run returned zero rows and reported only `request failed — `
with nothing after the dash. Four separate defects, all now fixed and
unit-tested (`test_south_africa_fetch_unit.py`), and all four are
worth knowing before touching it again:

1. **The API's response time scales badly with page size.** Measured:
   `PageSize=200` never returns (still timing out at 130s);
   `PageSize=50` answers in ~60s. The original 200-at-45s combination
   could not have succeeded. Constants are now `SOUTH_AFRICA_PAGE_SIZE
   = 50`, `SOUTH_AFRICA_TIMEOUT_SECONDS = 120`, plus
   `SOUTH_AFRICA_MAX_PAGES = 4` so one HTTP request can't run for
   many minutes.
2. **httpx timeout exceptions can carry an empty `str()`** — which is
   why the error message ended at the dash and a timeout was
   indistinguishable from a DNS failure. `_request_error` falls back
   to the exception class name.
3. **The API answers 200 with a non-JSON body** on later pages.
   `json.JSONDecodeError` is neither `HTTPStatusError` nor
   `RequestError`, so it escaped both handlers and took the run down
   with a 500 instead of being recorded as a partial failure.
   `_decode` converts it into the normal partial-failure path.
4. **`DENEL` was missing from `DEFENCE_ORG_PHRASES`** — South
   Africa's state-owned defence prime, found by checking the feed's
   own distinct buyer names against the filter rather than trusting
   the filter's output.

Also: OCDS statuses `cancelled`/`withdrawn`/`unsuccessful` are real
statuses for tenders nobody can bid on, and `programmes.stage` has no
state meaning "called off". They now raise `NotAnOpportunity` and are
**skipped like a non-defence buyer**, not counted as parse failures —
which is what `normalize_batch`'s docstring always claimed and the
code did not do. This matters in practice: the first working run
found exactly two defence-relevant releases in 30 days and both were
cancelled, previously reported as "failures" (reads as a broken
normalizer) rather than "the tenders were called off".

`fetch_all_releases` keeps the same partial-failure contract as every
other source, so a slow/failed later page still keeps whatever
earlier pages already succeeded.

`colombia` (SECOP II, Colombia Compra Eficiente) is the seventh
source, the first in Latin America, and the first since CanadaBuys
whose codes feed matching directly — it publishes **UNSPSC**, so it
needs no organisation sentinel and plugs straight into the existing
`taxonomy_unspsc_mapping` longest-prefix walk. It is also the **third
source able to populate `contract_awards`** (after TED and UK Find a
Tender), because the winning supplier is published inline.

The fetch is a **Socrata SoQL query**, not a bulk download
(`www.datos.gov.co/resource/p6dx-8zbt.json`), so the buyer and date
filters run server-side and a run transfers a few hundred relevant
rows rather than the national procurement history. `build_where()` is
therefore part of the contract — but it is a pre-filter only;
`colombia_normalize.is_defence_buyer` re-checks every row, including
the accent folding SoQL cannot express. No key needed;
`SOCRATA_APP_TOKEN` is optional and only raises the rate limit, which
is why it is deliberately **not** declared as `api_key_env_var` (that
would make the scheduler skip the source when it is absent). HTTP 429
is expected without a token and is retried with backoff, not treated
as a failure.

**Colombia is the only source needing a three-part relevance test**,
and each part came from live data, not caution:

1. **"DEFENSA" in a name is not evidence of defence.** Two of the
   biggest matches for that word are civilian: an environmental
   authority ("...PARA LA DEFENSA DE LA MESETA DE BUCARAMANGA") and
   the state legal-defence agency ("AGENCIA NACIONAL DE DEFENSA
   JURÍDICA"). Excluded *before* the phrase list.
2. **Buyer filtering alone does not work here**, unlike CanadaBuys and
   CPPP. Of 549 live military-buyer rows, 339 were "Prestación de
   servicios" — individual contractor hiring whose procedure name is
   often just a person's name. A goods test on `tipo_de_contrato` is
   required as well.
3. **Military health/welfare units dominate by volume** (dispensarios,
   hospitales navales, jefaturas de salud, liceos). Genuinely
   military, genuinely not capability procurement — dropped on the
   same judgement as MES estate works and CFB Halifax construction.

Other real shapes in this feed: `urlproceso` is a nested object
(`{"url": ...}`), not a string; the UNSPSC code carries a `"V1."`
version prefix and some rows say `UNSPECIFIED` instead; and
`adjudicado = 'Si'` is **not** sufficient evidence of a winner — 14 of
36 awarded rows named the supplier as the literal string `"No
Definido"`, which would otherwise have become an OEM organisation
credited with contract awards. Statuses `Cancelado`/`Desierto` get the
same treatment as South Africa's cancelled OCDS statuses: skipped, not
failed.

Known limitation, verified end-to-end: Colombian titles are Spanish
("COMPRA MATERIAL AERONAUTICO"), and `capability_taxonomy_keywords` is
English, so Colombian matches are almost always **code-only** and
therefore capped at low confidence by design. First live run: 1,282
rows examined → 308 programmes → 58 awards, and a UK/US aerospace
product matched 42 Colombian programmes (the largest single source for
that product). **Spanish keywords were added** (`db/migrations/028_spanish_keywords.sql`)
— 61 terms grounded in the actual text of the 292 ingested Colombian
programmes (frequency-checked before inclusion, e.g. "aeronáutico" in
40 titles, "repuestos" in 21), not a dictionary. Four high-frequency
words were deliberately rejected after checking what they actually
match: "naval" (35 hits, but almost always the buyer's own base name,
not the subject), "cartucho" (printer cartridges as often as
ammunition), "bote" (soft-drink bottles), "motor" (too generic, and
redundant with "automotores" under word-boundary matching anyway).

This required one small but real change to `app/scoring.py`: both the
programme title and every keyword are now passed through `fold()`
(NFKD normalize, strip combining marks, lower-case) before matching,
because the feed itself is inconsistent about accents — the same
force appears as both "EJERCITO" and "EJÉRCITO". `fold()` is the
identity function on plain ASCII, so no English keyword or English
title's match result changes; verified by running the full suite
(325 passed, same single pre-existing CORS failure) and confirmed
live: an aerospace product's Colombian matches moved from all "low"
confidence to 3 of 6 "high" once "material aeronáutico" started
scoring. `score_text` has exactly two callers — `classify_text`
(Phase 1) and `programme_matching.py`'s keyword re-score — both
covered by the existing test suites, so nothing else in the
codebase depends on the un-folded behaviour.

`prozorro` (Ukraine, api.openprocurement.org) is the eighth source,
first in Eastern Europe, and architecturally the most different from
every other one: its public sync API supports **no server-side
filter at all** — confirmed live by sending `classification_id=` and
`opt_fields=classification` and finding both silently ignored. It is
a raw changes feed meant for building a full local mirror, not a
queryable dataset, so this is the only source built as a genuine
**two-phase fetch** (`app/prozorro_ingestion.py`): a cheap LIST scan
(the only fields `opt_fields` actually honours there are
procuringEntity/status/tenderID — requesting `title` or `items` is
silently dropped) filtered client-side to defence-institution buyers,
then one GET /tenders/{id} DETAIL fetch per surviving candidate to
get title/items/classification/awards, none of which the list
endpoint will ever return.

Buyer filtering alone is not enough here (same problem as Colombia,
worse in degree): a live 3,000-row sample found 23% of recent
releases came from a defence institution — the highest hit rate of
any source — but the majority was military units (the dominant
pattern, "військова частина", literally present in every such buyer's
name regardless of unit code) buying underwear, potatoes and office
laptops for internal use. Relevance is therefore a three-part test —
defence buyer, not a health/welfare unit, and a materiel-relevant CPV
code — reusing `uk_ft_normalize.is_defense_relevant_cpv` directly
rather than a second drifting copy of the same list, since Ukraine's
ДК021 scheme IS the EU's CPV under a translated label (confirmed
live: "18310000-5" prints "Спідня білизна", matching CPV 18310000
"Underwear" exactly).

**`app/works_filter.py`'s `is_uninformative_title` must never be
called on this source's titles** — it tests for
`[A-Za-z]{4,}\s+[A-Za-z]{3,}`, which matches zero characters in
Cyrillic text, so it would have flagged every genuine Ukrainian title
as "uninformative" and silently deleted the entire source. Caught
before it shipped by checking the regex against real Cyrillic
fixtures, not after.

Because each candidate needs its own live HTTP call, and this is a
shared national production feed processing on the order of 200 tender
events per hour, the per-run caps are small relative to every other
source (`PROZORRO_LIST_MAX_PAGES = 6`,
`PROZORRO_MAX_DETAIL_FETCHES = 150`, a 0.15s delay between detail
fetches) — each run genuinely only covers the most recent slice of
national activity, not a full day, and coverage accumulates across
scheduled re-runs via the same upsert every other source uses. First
live run: 150 candidates detail-fetched, 5 kept as genuinely
materiel-relevant, 0 failures.

`cppp_india` is the odd one out and deliberately so. India's CPPP
publishes no API and no classification scheme, so this source parses
HTML (`parse_rows` in `cppp_india_normalize.py`), is bounded by
**pages** rather than a date window, and decides defence-relevance
from the **publishing organisation** (Military Engineer Services,
BSF, DRDO, defence PSUs…) instead of a category code. Matched rows
carry the sentinel code `IN-DEF` in `programmes.naics_code`.
The portal rate-limits — pages are fetched sequentially with a delay
and a hard cap; don't raise them to speed a run up. Pagination must
go through the portal's own base64 `/cpppdata?url=` pager form and
requires `follow_redirects=True`; a bare `?page=N` silently fails.
GeM, DEFPROC and data.gov.in were each assessed and rejected for
concrete reasons recorded in `db/migrations/014_cppp_india_source.sql`
and the `cppp_india_ingestion.py` docstring — read those before
proposing them again. Note the compliance item flagged there:
written permission for commercial reuse has not yet been sought
from NIC.

Rotation (`get_rotation_index`/`advance_rotation_index`) is per-source via an
explicit `rotation_key` string — only advances after a real attempt
genuinely completes, so a config error never silently skips a group.

**NAICS coverage gap fix (2026-09, migrations 031/032 + `sam_gov_normalize.py`)**:
a Report Intel review found the real reason several capabilities
(Naval, Land Systems, Comms, Aerospace, Space, Cyber, MRO) showed
almost no live SAM.gov data was NOT `taxonomy_naics_mapping` —
several already had correct codes mapped — it was that
`DEFENSE_RELEVANT_NAICS` (the codes SAM.gov's `ncode` rotation
actually queries for) held only 6 codes, so those capabilities'
mapped codes were simply never asked for. Verified live: of 3,514
ingested programmes, only 5 distinct NAICS codes appeared anywhere in
SAM.gov data. Expanded to 14 codes (each verified against naics.com/
Census.gov/IBISWorld before adding) — the mapping-table fix alone
would have changed nothing without this. `taxonomy_unspsc_mapping`
was deliberately expanded much less (one verified family, ammunition)
— granular 8-digit UNSPSC codes have no equally reliable public
lookup the way NAICS does, and a web check on a real, frequent
Colombian code returned a result contradicting the ingested title
text; left unmapped rather than guessed. See the migrations'
own headers for the full reasoning.

### Programme matching (`api/app/programme_matching.py`, `api/app/matching_scoring.py`)

Matches a product's **confirmed** capabilities to programmes via two unioned
signals: `taxonomy_naics_mapping` (US) and `taxonomy_cpv_mapping` (UK) — both
curated v1 starting sets, not exhaustive — plus a keyword re-score of the
programme's own title.

Canada adds a third scheme, `taxonomy_unspsc_mapping`, matched by
**prefix rather than exact equality**. That is not a shortcut: UNSPSC
is hierarchical by construction (8 digits = Segment/Family/Class/
Commodity), tenders carry specific commodity codes like `25172800`
that no curated table could enumerate, so the mapping stores family
prefixes like `2517` and matching walks down from them. Adding a
scheme therefore means touching `_load_candidate_programmes` — check
whether it needs exact or prefix semantics before copying either.

India and South Africa are a third case with no mapping table: the
`IN-DEF` and `ZA-DEF` sentinels (together, `ORGANISATION_SENTINEL_CODES`
in `app/programme_matching.py`) are added to *every* capability's code
set, which is what makes CPPP/eTenders SA programmes candidates at
all. Because a sentinel is identical on every row from that source it
distinguishes nothing between capabilities, so a sentinel match with
**zero keyword corroboration is dropped outright** rather than kept as
a low-confidence match (which is what happens for a real NAICS/CPV
code-only match). Removing that guard would make every tender from
that source match every capability. A classification-code-only match with no keyword
corroboration is capped at "low confidence" (fixed after a real false-positive
was found in live SAM.gov data — e.g. category-only matches producing
nonsense results). The API response field is `naics_match` for backward
compatibility even though it now means "matched via any classification code,
NAICS or CPV"; `matched_classification_code` is the newer, honestly-named
field.

Known naming debt: `programmes.naics_code` stores CPV codes for non-US
sources too. Don't assume the column name reflects its content for every row.

### Procurement contacts now include a postal address
(`db/migrations/029_contact_address.sql`, `app/address_format.py`)

`programmes.contact_address` sits under the exact same scope limit as
`contact_name`/`contact_email`/`contact_phone` (migration 017): read
only via the one programme/opportunity it belongs to, never listed,
searched, or aggregated across tenders. `format_address()` is a tiny
pure helper (its own module, for the same "no sqlalchemy at module
level" reason `app/scoring.py` and `app/works_filter.py` are separate
modules — see `app/capability_resolver.py`'s docstring) that joins
whatever parts a source actually published, skipping missing ones
rather than leaving gaps.

**Coverage differs genuinely by source — verified, not assumed:**
- **SAM.gov** previously extracted NO contact information at all,
  despite being the single largest source (769 programmes) — a real
  gap, not a stylistic omission. `pointOfContact[]` (name/email/phone,
  preferring the entry marked `"primary"`) and `officeAddress`
  (city/state/zip/country) are both real, documented fields this
  project was already fetching and simply never reading. Deliberately
  reads `officeAddress`, the CONTRACTING OFFICE's address — **not**
  `placeOfPerformance`, a different field for where the awarded work
  happens, often a different city or country entirely; using it here
  would send a supplier to the wrong place.
- **UK Find a Tender**: the buyer party's `address` uses the identical
  OCDS Address object already relied on for supplier addresses
  (`extract_winners`) — same schema, different role.
- **Colombia (SECOP II)**: no street-level address is published, only
  `ciudad_entidad`/`departamento_entidad` (city/department) — real
  government-office location at a coarser grain, kept rather than
  left blank.
- **ProZorro (Ukraine)**: `procuringEntity.address`, confirmed live to
  be a consistently-populated, REQUIRED-looking field — more reliably
  present than on any other source.
- **CanadaBuys and South Africa**: genuinely no address field exists
  in either feed (confirmed live for South Africa: `procuringEntity`
  there carries only `id`/`name`) — left blank rather than guessed.
- **EU TED**: not attempted. TED's search API takes an explicit list
  of field IDs (`TED_FIELDS` in `app/ted_eu_ingestion.py`), and
  guessing a wrong buyer-address/contact field ID risks breaking the
  single biggest source in this platform (2,029 programmes) without a
  live test to catch it first. Left as a known, stated gap rather than
  risked.

### Phase 4 — capability taxonomy expansion (2026-09, migrations 033/034)

`TACTICAL.PROTECTIVE_EQUIPMENT` was added after a review queried this
platform's OWN ingested data for classification codes appearing
frequently but matching no existing capability — the single most
coherent, highest-volume real gap found (body armour, combat
uniforms, military helmets, bullet-proof vests; ~168 live TED
programmes). CPV codes and their labels are TED's own printed labels
on real notices (same verification method as migration 025); NAICS
339113 ("Surgical Appliance and Supplies Manufacturing") was checked
against multiple independent sources before adding — not obvious from
its title alone, it genuinely includes body-armor manufacturing.

**A real bug was found immediately by live-testing the new capability
end to end**, not by inspection: a test product confirmed for it
produced 168 matches with ZERO at high confidence. Cause: every
keyword added in 033 was singular ("military helmet", "bullet-proof
vest"), but this platform's own ingested TED titles print these CPV
labels PLURAL 100% of the time ("Poland – Military helmets – …").
`app/scoring.py`'s word-boundary regex (`\bKEYWORD\b`) requires a
boundary immediately AFTER the keyword too, so "military helmet"
does not match inside "Military helmets" — no boundary exists between
"t" and the following "s". Migration 034 adds the plural forms
explicitly; after it, the same test product went from 0 to 61
high-confidence matches. `UAV.INTEGRATION`'s existing keyword list
sidesteps this by using "unmanned aerial" (no trailing noun) rather
than "unmanned aerial vehicle" — worth knowing this is not
necessarily fixed platform-wide, only for this one capability.
A systemic fix (teaching `score_text`'s regex to accept an optional
trailing "s") was considered and deliberately deferred rather than
applied under time pressure — it would touch matching behaviour for
all 22 capabilities at once and deserves its own careful regression
pass, not a same-turn change riding on one capability's fix.

Also found and deliberately NOT auto-fixed: the same CPV neighbourhood
carries a much larger volume of police/fire-brigade equipment
(Police uniforms, Fire-brigade uniforms, Fire engines — 26/60/97+27
live programmes) ingested under UK Find a Tender and TED's
"the entire CPV division 35 is defence/security-relevant" rule. Only
the genuinely military-specific codes were mapped to the new
capability; whether the whole-division ingestion rule should be
narrowed is a separate, platform-wide relevance question left open
for a human decision (see migration 033's header).

### Customer Intelligence — organization dedup (2026-09, migration 035)

A review into "is buyer fragmentation a real problem" — prompted by
an earlier speculative worry about MoD/Ministère de la Défense/
Minister of National Defence looking like duplicates — went and
CHECKED live rather than assumed. That specific worry was unfounded
(each names a genuinely different country's ministry). What WAS real,
found by querying for exact- and whitespace-duplicate `(name,
org_type)` pairs: a Ukrainian military unit name inserted twice
byte-for-byte identical (a genuine race — two concurrent ingestion
calls both found "not found" before either INSERT committed), and a
SAM.gov DLA Aviation Ogden office inserted twice differing only by a
double space in `fullParentPathName`.

**Root cause**: all 8 ingestion modules ran their own private
check-then-insert against `organizations`, with no database-level
constraint stopping two such sequences from racing. Fixed at the
source, not papered over: `organizations` now has a real
`unique(name, org_type)` constraint (migration 035, after merging the
two known duplicate pairs — repointing every FK from `programmes`,
`opportunities`, `contract_awards` and `contacts` onto the surviving
row), and every one of the 8 modules' near-identical private
`_get_or_create_organization` functions was deleted in favour of two
shared functions in `app/ingestion_common.py` —
`get_or_create_government_buyer` (new) and the pre-existing
`get_or_create_oem_organization` — both now upsert with `ON CONFLICT
(name, org_type) DO UPDATE ... RETURNING id`, making the race
structurally impossible rather than merely unlikely. Deliberately
whitespace-only normalization (`app/org_name_format.py`, its own pure
module for the same "no sqlalchemy at import time" reason
`app/address_format.py` exists) — no accent-folding, no fuzzy/semantic
matching, because that risks merging two REAL, DIFFERENT buyers into
one, which is worse than the fragmentation it fixes. That broader
question (should genuinely differently-worded duplicates ever be
merged) was deliberately left as a human decision, not resolved here.

### Tender Briefing contact depth + company profile editability (2026-09, migration 036)

Two independent additions from the same review:

- **`programmes.contact_email_secondary`**: SAM.gov's `pointOfContact`
  array genuinely carries a `type: "secondary"` entry on real notices
  alongside the primary one (confirmed against GSA's own documented
  example) — read now, shown in the Tender Briefing as "Email
  (Secondary)" next to the primary. Every other source publishes only
  one contact, so this stays null for them, honestly.
- **`tenants.linkedin_url`, and `company_name`/`website`/`country`/
  `phone` are now genuinely editable** via `PATCH /company/profile` —
  they were returned by `GET` but had no way to be corrected before
  this. Real motivating bug: a company name saved with a typo had no
  fix path. `email` is deliberately excluded from this route on
  purpose — it lives on `users`, not `tenants`, and is never touched
  here. `linkedin_url` is validated for SHAPE only
  (`linkedin.com/company|in|school/...`) and is never marked
  "verified" anywhere in the response — this platform has no LinkedIn
  OAuth integration to confirm the page actually belongs to the
  company, and the evidence model here exists specifically to prevent
  claiming verification that isn't real. What IS genuinely enforced:
  only a signed-in admin of that tenant can set or change it — the
  same identity binding every other company-profile field already
  relies on. Shown on the public share card too (unlike email/phone,
  a LinkedIn company page is public marketing material, not a private
  contact channel) and on the post-registration vetting profile.

### Engagement Intelligence — team visibility (2026-09)

The remaining real gap a Report Intel review found: `owner_user_id`
had existed since the Phase 0 schema and was always PATCH-settable,
but nothing ever listed a tenant's own teammates — it was, in
practice, write-only, since no picker could exist without a way to
see who to assign. `GET /team/members` closes this (explicit
`tenant_id` filter, not RLS — confirmed live that `users` carries no
RLS policy at all, unlike `products`/`opportunities`/`audit_log`, so
every query against it in this file has always had to scope itself
by hand). `GET /engagement/team-workload` groups every OPEN
opportunity by owner, including an explicit "Unassigned" bucket
(itself a real signal — work nobody has claimed) with an overdue
count per owner.

**A real bug was found wiring the "Unassigned" option into the
picker, not by inspection**: `owner_user_id: null` on its own means
"not part of this PATCH" — the same rule `checklist_state` already
documents — so selecting "Unassigned" and saving silently did
nothing. Fixed with an explicit `clear_owner: bool` on
`OpportunityUpdateIn` rather than overloading `None` to mean two
different things depending on which field it's on.

`suggest_next_action` also gained `due_date` overdue-awareness,
stacking with the existing set-aside/owner prefixes rather than
adding a new branch: `due_date` is the TENANT's own internal
follow-up date, genuinely different from `response_deadline` (the
GOVERNMENT's tender deadline, already handled) — it existed and was
settable since Phase 0, but nothing ever checked whether it had
quietly passed.

### Source volume imbalance — diagnosed, not guessed (2026-09)

A fair challenge: total ingested volume (~4,800 programmes) is
genuinely dominated by 2 of 9 sources (TED 2,024 + CanadaBuys 1,647 =
77%), while CPPP/ProZorro/UK-FT/South Africa combined sat around 80.
Rather than accept that as "just how it is" or immediately start a
10th source, each thin source was individually diagnosed live to find
out whether it's a real technical limit or a fixable bug:

- **CPPP — a real, fixable bug, now fixed.** Live runs were
  consistently dying around page 19-22 with "Server disconnected
  without sending a response", always after roughly 300-350s of
  sustained connection time (each page genuinely takes ~15-16s — the
  portal itself is slow) — a session/connection-duration limit, not a
  timeout or this project's own 1s inter-page delay. `fetch_listing_pages`
  now RECONNECTS with a fresh `httpx.AsyncClient` on a dropped
  connection and resumes the SAME page, up to `CPPP_MAX_RECONNECTS`
  (5) times, instead of aborting the whole run. Verified live: a full
  40-page run that used to fail partway through completed cleanly
  end to end (`errors: []`, `pages_requested: 40`).
- **CPPP — a second, deeper bug: no page rotation, now fixed.**
  Fixing the disconnect alone barely moved net-new coverage — a full
  40-page run afterward added only 3 genuinely new programmes on top
  of 61 already stored. Root cause: this portal has no date filter
  (see the source's own long-standing documentation), so every run
  always started at page 1 and mostly re-scanned the SAME top pages.
  Given the exact rotation mechanism SAM.gov's NAICS-code cycling
  already proved (`app.ingestion_common.get_rotation_index`/
  `advance_rotation_index`), the same pattern was applied to a PAGE
  OFFSET instead of a code group (migration 037 seeds the state row).
  Verified live: successive runs now start at page 6, not page 1.
- **UK Find a Tender — near its honest ceiling, not a bug.** Widening
  the one-time backfill window from the default 7 days to 90 and then
  365 found 5 records both times — the same number — meaning the
  available "planning,tender,award" defence-relevant notices within
  this platform's CPV filter are already being found. A single
  country's volume is expected to sit far below TED's 27-country
  aggregate; this is a real, small source, not an under-realized one.
- **South Africa — near its honest ceiling too.** A 90-day window
  examined 108 releases and found 0 defence-relevant ones this time —
  consistent with the documented ~1-2-per-100 ratio (see the source's
  own entry above), not new evidence of a problem. Its own per-page
  API latency (60-90s+) already makes widening `SOUTH_AFRICA_MAX_PAGES`
  further a poor trade for the likely yield.
- **ProZorro — working as designed, needs time not a fix.** Confirmed
  live that repeated runs add genuinely DISTINCT tenders (10 stored,
  10 distinct external_ref, zero wasted re-fetches of the same
  candidate) — the architecture accumulates correctly; a national
  feed this size (~200 events/hour) was never going to be swept in
  one run, by design (see the source's own entry above).

### Orphaned ingestion jobs and container self-healing (2026-09)

A user-reported "records not increasing" turned out to be two separate,
real findings once checked live rather than assumed.

**Orphaned jobs — a real bug, found and fixed.** An `ingestion_jobs`
row is only ever marked `'failed'` by its own run function's
`except` block — which only runs if the Python code itself gets a
chance to handle the error. A process death mid-run (this session's
own repeated `docker compose restart api`, and historically CPPP's
pre-reconnect-fix disconnects) severs the connection without ever
reaching that block, leaving the row `'running'` forever. Found live:
10 rows stuck over a day old across CPPP (x6), UK Find a Tender,
South Africa and ProZorro. Fixed with `_reap_orphaned_ingestion_jobs`
in `app/main.py`, run at startup: any row still `'running'` at that
point is provably orphaned (nothing resumes a job by ID, so a fresh
process was never going to continue it), and gets marked `'failed'`
with `error = 'orphaned — API process restarted mid-run'`. Verified
live twice — once cleaning the 10 real stuck rows after a rebuild,
once confirming a fresh `docker kill` produces zero new orphans once
the container comes back (because nothing was actually mid-run at
kill time).

**Container self-healing — re-tested 2026-09-19, and this time it
surfaced a real, more specific reason it doesn't reliably self-heal
in this dev sandbox, not just "didn't observe a restart".** `restart:
unless-stopped` on both services in `docker-compose.yml` is correctly
saved (`docker inspect` confirms `RestartPolicy.Name: unless-stopped`)
and Docker genuinely does retry — `RestartCount` climbed on this test,
proving the policy is active, not inert. But the API's own startup
depends on resolving the `db` hostname via Docker's embedded DNS, and
in this sandbox (Docker Desktop over WSL2) that resolution can fail
right after a kill (`socket.gaierror: Name or service not known` —
confirmed in `docker compose logs api`), crashing the process again
before it ever reaches a healthy state; the *next* backed-off retry
then hits the same race again, and the interval between attempts
grows each time. Live-confirmed: `docker compose ps` showed the `api`
container gone entirely ~20s after a `docker kill`, `/healthz` refused
the connection, and it did not come back on its own within a further
30s wait — had to run `docker compose up -d api` by hand. This is a
sharper, evidence-backed version of the same honest gap already
recorded here: the policy is correctly configured and DOES retry, but
whether it reliably WINS that DNS race depends on the host's Docker
networking stack, which is genuinely worse in this WSL2 sandbox than
on a standard Linux Docker host — don't assume a killed container
will actually be back without checking `docker compose ps` first.

**A real reminder about this project's own dev setup, found while
debugging why a code fix "didn't take effect": the `api` service has
no bind-mounted source volume** — `docker compose restart api`
restarts the SAME already-built image and picks up NOTHING; only
`docker compose up -d --build api` (or a full `up --build`) bakes in
a source change. Several `restart`-based verifications earlier in
this project's history were silently checking old code as a result
of this — worth remembering before trusting a "restart and re-verify"
result again.

### AusTender (Australia) — ninth real source (2026-09, migration 038)

Found during the same Brazil/Chile research pass that turned up two
real, live-verified blockers (Chile's live API needs a Clave Única,
Chile's own national digital ID, to even request a ticket; Brazil's
PNCP open-tender endpoint failed or timed out on every live attempt
with a non-empty date range). AusTender was a genuine, no-friction win
by comparison — `api.tenders.gov.au`, no key, real OCDS 1.1 shape,
cursor pagination via `links.next` (same pattern already proven for UK
Find a Tender / South Africa).

**Live-verified before building, not assumed**: a 100-row sample found
55 from "Department of Defence" as procuringEntity — the highest
buyer-filter hit rate of any source here — with genuinely
materiel-relevant descriptions ("Pump Repair", "Battery Charger
Repairs", "Transformer Repairs"). Every contract item carries a real
UNSPSC code, so — like CanadaBuys and Colombia — this needs no
organisation sentinel and plugs straight into the existing
`taxonomy_unspsc_mapping` longest-prefix walk.

**A real, stated scope limit**: this endpoint (`findByDates/
contractPublished`) is CONTRACT NOTICES ONLY — post-award, not a
pre-award tender pipeline. Every programme from this source is
therefore ingested directly at `stage = 'contract_awarded'`. AusTender
does publish pre-award "business opportunities" on its own portal, but
no equivalent open API for that was found or verified — left as a
stated gap, the same discipline every other source's documented gaps
in this file already follow. Because it's contract-notice data, the
winning supplier is on every single row (not a subset needing a guard,
unlike Colombia's literal "No Definido" case) — the SIXTH source able
to populate `contract_awards`.

**A real data-quality finding caught by checking, not assumed present
just because the schema has a place for it**: the OCDS `parties`
object for the procuring entity DOES carry `address` and
`contactPoint` fields, and it would have been easy to wire them
straight into `programmes.contact_*` the way SAM.gov's and ProZorro's
contact depth was added earlier this session. Checked live across
every Department of Defence row sampled first — `address` is always
an empty object and `contactPoint` is always the exact same literal
`"tenders@finance.gov.au"`, a shared department-wide inbox, never
anything specific to the buying unit. Deliberately left as `None`
rather than shown: a byte-identical "contact" repeated on every single
programme from this source would look like fabricated data to a user
reading Tender Briefing across many different Australian tenders — the
exact failure mode this platform's evidence model exists to prevent.

**Relevance test**: buyer name alone, same as CanadaBuys/CPPP — only
`"Department of Defence"` was confirmed live in the sampled window.
Other genuinely Australian defence bodies (e.g. the Australian
Submarine Corporation, Defence Science and Technology Group) may exist
under their own party names but weren't observed and are deliberately
NOT guessed into the filter — the same "don't add a name that wasn't
actually seen" discipline already applied to Colombia's and South
Africa's buyer-phrase lists.

First live run (`days_back=90`, `AUSTRALIA_MAX_PAGES=20` → 2,000
releases examined): **1,088 programmes ingested, 1,088 awards
recorded, zero failures, zero errors, 38 seconds** — the single
largest first-run yield of any source built this session, and
immediately confirmed resolving to real capabilities on day one
(LAND.SYSTEMS, NAVAL.SYSTEMS, MANUFACTURING.DEFENCE, SENSORS.GENERAL,
SENSING.RADAR, CYBER.DEFENCE via live DB query against
`taxonomy_unspsc_mapping`), including a real, recognisable defence
prime among the winners (SAAB AUSTRALIA PTY LTD).

### DNCP Paraguay — tenth real source (2026-09, migration 039)

Found continuing the Brazil/Chile/Peru/Mexico research pass. All four
of those were assessed live and rejected or deferred: Chile needs a
Clave Única (national digital ID) even to request an API ticket;
Brazil's PNCP open-tender endpoint failed or timed out on every live
attempt; Peru's OECE API and download domain both returned a live
HTTP 403; Mexico's documented federal API
(`api.datos.gob.mx/v1/contratacionesabiertas`) genuinely timed out
from this environment, confirmed via a control request against a
known-working API in the same session. Paraguay's DNCP v3 API
answered immediately with real data and needed **no registration at
all** for the read/search endpoints used here, despite its own
Swagger spec declaring a global Bearer Auth requirement — verified
live, not assumed from the docs. (A self-service account, email or
Google/GitHub sign-in, does exist at `/datos/adm/login` should the
unauthenticated rate ever need raising — the docs' own claim of "4
calls/sec allowed without a key for testing" held up in practice.)

A live sample under "Ministerio de Defensa Nacional" found real
materiel procurement carrying genuine US MIL-SPEC part numbers (item
description "EMPAQUE MS28775-011", country-of-origin attribute
"USA" — MS28775 is a real aerospace O-ring specification, not a
coincidental string match) and, on a larger sample, a real UNSPSC
code with a real description ("73161607" = "Servicios de fabricacion,
reparacion y mantenimiento de aviones y transportes aereos" — aircraft
MRO services).

**Relevance** follows Colombia's shape, not CanadaBuys/CPPP's: buyer
filtering alone is not enough. A live sample under "Ministerio de
Defensa Nacional" mixed real materiel (vehicle parts, military
vehicles, aircraft parts, arms-registry supplies) with generic
institutional procurement (kitchen utensils, office folders, firewall
software licences, building maintenance, food supply, photocopier
rental, insurance, ceremonial/educational materials). Fixed with a
category-level exclusion (`app/paraguay_normalize.py`'s
`NON_MATERIEL_CATEGORY_PHRASES`) built from DNCP's own printed
category text on what was actually observed — including two phrases
added on a SECOND live sample after the first pass let "Servicios
basados en ingenieria investigacion y tecnologia" (DNCP's catch-all
for IT/software services) and a ceremonial-materials category through
despite genuinely non-materiel titles ("ADQUISICIÓN DE SERVICIOS Y
LICENCIAS FIREWALL...", "Servicio de Ceremonial").

**Classification** is genuinely UNSPSC, published two ways on the same
item: DNCP's own "catalogoNivel5DNCP" scheme (an 8-digit UNSPSC code
plus a local suffix, e.g. "73161607-001") AND a bare
`additionalClassifications` entry with `scheme: "UNSPSC"` and no
suffix at all — the latter is read first since it needs no parsing,
falling back to stripping the former's suffix. Both feed the existing
`taxonomy_unspsc_mapping` longest-prefix walk unchanged, same as
CanadaBuys, Colombia and Australia. Awards are published inline
(`awards[].suppliers[].name`), making this the EIGHTH source able to
populate `contract_awards`.

**Two genuinely new engineering traps, both live-confirmed and
fixed, neither a timeout or a clean disconnect** (both already-solved
failure shapes from CPPP/South Africa):

1. **Intermittent mid-JSON truncation on an otherwise-normal HTTP
   200.** The connection completes, the status is 200, and the body
   is simply cut off part-way through an object — confirmed
   repeatedly, worse at larger `items_per_page` but not eliminated
   even at 10. `_fetch_page` treats a JSON `ValueError` as retryable
   (small backoff, re-request the SAME page) rather than a hard
   failure.
2. **A genuine dropped connection on top of that**, live-confirmed on
   a 90-day first run ("Server disconnected without sending a
   response" after page 1). `fetch_processes` now reconnects with a
   fresh `httpx.AsyncClient` and resumes the SAME page, up to
   `PARAGUAY_MAX_RECONNECTS` — same recovery shape as CPPP's
   reconnect logic, for a different underlying cause.

**A real, self-found bug in this module's own first version, caught
by testing the function against a live fetch rather than trusting an
earlier manual read**: `/search/processes`'s own `compiledRelease`
carries NO `items` at all — confirmed live — so classification codes
need a second, per-candidate `GET /ocds/record/{ocid}` call (the same
two-phase LIST-then-DETAIL shape ProZorro already uses, for the same
reason: its list endpoint is equally thin). The first implementation
of `extract_classification_from_detail` assumed that endpoint's
response was a bare release package (`{"releases": [...]}`) and
silently extracted nothing from a genuinely live, 200-OK, correctly-
parsed response — the HTTP layer reported success throughout, so this
was NOT a "some request failed" bug, it was a "every request
succeeded and the data was simply never being read" bug. The real
shape is a RECORD package (`{"records": [{"ocid", "releases",
"compiledRelease"}]}`); fixed to read `records[0].compiledRelease`.
Caught immediately by testing the extraction function directly against
a known-real ocid (`{"ocid": "...", "code": None}`) rather than only
checking the HTTP status of the ingestion run, which had reported
"succeeded" throughout — a reminder that a job's own success status
only means "nothing raised an exception," not "the data is what was
intended."

`PARAGUAY_MAX_DETAIL_FETCHES` (20) and `PARAGUAY_MAX_PAGES` (8) were
both lowered from initial, more generous values after a live run at
the higher settings exceeded a reasonable single-request duration
(400s+, had to be abandoned mid-flight — caught cleanly by
`_reap_orphaned_ingestion_jobs` at the next restart, not a
data-correctness problem). Coverage of both the search phase and the
classification-detail phase accumulates across scheduled re-runs, the
same page/detail-cap pattern every capped source here already uses.

First clean live run (`days_back=180`, after all of the above fixes):
**46 programmes ingested, 25 awards recorded, zero errors** — 19 of a
sampled 88 total programmes carrying a real UNSPSC code (the rest
predate the classification-detail-fetch fix, or exceeded that run's
own `PARAGUAY_MAX_DETAIL_FETCHES` cap), 3 of those already resolving
to real capabilities (`LAND.SYSTEMS`, `NAVAL.SYSTEMS`) on live query
against `taxonomy_unspsc_mapping`.

### Fit & Feasibility Score (2026-09, migration 040)

A user-requested feature, built to a specific constraint the user
set explicitly after discussion: it must never be called a
"probability" of winning, because a real probability needs real
historical Won/Lost outcomes to calibrate against, which this
platform does not have at scale yet. `app/fit_score.py` is a pure,
rules-based module (same separation as `scoring.py`/
`matching_scoring.py`) that computes a 0-100% "readiness signal"
across four scored parameters, with a fifth acting as a hard gate
rather than a blended weight:

- **Match confidence** (40 pts) — reuses the existing
  high/medium/low signal.
- **Value-tier fit** (20 pts) — an all-or-nothing presence signal:
  full points if this buyer has ANY real captured award-value
  history (see below), because the goal is "do we have grounded
  evidence of this buyer's typical deal size", not a judgement on
  whether that size is attractive — only the tenant's own business
  knowledge can decide that.
- **Deadline feasibility** (20 pts) — banded by days remaining on
  `response_deadline`.
- **Buyer competitiveness** (20 pts) — `count(distinct
  winner_organization_id)` from `contract_awards` for this buyer:
  5+ distinct winners = open field, 2-4 = moderately concentrated,
  1 = possible incumbent relationship.
- **Stated restriction** (`set_aside_code`/`description`) — NOT
  scored/blended in. A real restriction the source states is "you
  may not be allowed to bid at all", categorically different from
  "somewhat less attractive", so it hard-caps the score at 20% and
  surfaces as its own banner with the restriction quoted verbatim.
  This module only ever shows what the source itself stated — same
  "not legal advice, confirm with export-control/trade-compliance
  counsel" stance as everywhere else in this platform (see Known
  Limitations). It never determines eligibility.

**Missing data is excluded, never defaulted to 0** — a parameter
with nothing to go on drops out of both the numerator and the
denominator, and the response states exactly how many of the 4
signals were actually available (`signals_available`/
`signals_total`), so a percentage never quietly means "mostly
missing data treated as bad".

**A real gap found while designing this, not before**: `contract_awards`
stored no award value at all — `value_amount`/`value_currency`
(migration 040) capture what two sources' raw responses already
carry but were discarding: AusTender (`contracts[].value:
{currency, amount}`) and SECOP II Colombia
(`valor_total_adjudicacion`, a column already named as a constant —
`COL_AWARD_VALUE` — but never actually read). Deliberately NOT wired
for two other sources rather than guessed at: DNCP Paraguay only
publishes value at the item level (no single award-level total
without inventing an aggregation rule) and CanadaBuys' award-file
column list has no value field at all — an earlier draft of this
migration wrongly claimed it did, caught and corrected before
applying rather than shipped. Colombia's value has no explicit
currency column; COP is used as a stated convention of the source
(Colombian public contracts are COP-denominated by law), not a
guessed value — only set when a real amount was actually parsed.

Wired into both `GET /opportunities` (list) and `PATCH
/opportunities/{id}` (detail/Manage panel) via a shared
`_attach_fit_scores` helper that batches the buyer-stats query once
across every distinct `organization_id` in the response rather than
one query per row. Live-verified end to end against real data: a
genuine SAM.gov 8(a) sole-source tender correctly capped at 20% with
the restriction quoted verbatim; a real Colombian Agencia Logística
tender scored 81% with a real buyer average (COP 295,582,870 across
43 awards) and real competitiveness signal (36 distinct winning
companies).

Frontend: a compact `%` badge in the Opportunity Dashboard's main
table (`fitScoreCell`) and the full breakdown + disclaimer on the
Manage panel (`renderFitScorePanel`) — the disclaimer text is always
rendered verbatim from the API response, never paraphrased in the
UI, so the two can't drift apart.

**Stated future step, not built**: as tenants mark real Won/Lost
outcomes on this platform (`opportunities.stage` already has both),
a v2 could calibrate an actual empirical win-rate model from that
data — a genuine probability at that point, unlike this one.

### Opportunity Dashboard — Active/Historical split (2026-09)

The "Hide already-awarded / in-service" checkbox (opt-in, off by
default) was replaced with a two-tab view: **Active** (default) and
**Historical**. A closed tender — already awarded/in-service per
the source's own `programme_stage`, OR its own published
`response_deadline` has passed (`isClosedTender`, a new client-side
check the old toggle never covered) — no longer needs an opt-in
click to get out of the primary pipeline view; it was real data
worth keeping (Competitor Intel, buyer history) but never something
a user should have to scroll past by default to find what's
actually still open. The Historical tab shows the same table
structure with a framing note explaining why each row is there
instead of live.

### Tender Briefing contact display — re-verified, not re-built (2026-09)

A user request to "show contact primary/secondary email with
address" turned out to already be fully wired (both the Tender
Briefing modal and the Manage panel already render
`contact_email`/`contact_email_secondary`/`contact_address` — see
this file's own earlier entry on migration 036). Re-verified live
end to end rather than assumed or silently rebuilt: inserted a real
test opportunity against a real SAM.gov programme with all three
fields populated (a genuine DCSA — Defense Counterintelligence and
Security Agency — tender) and confirmed the API response and both
frontend render paths surface primary email, secondary email and
address correctly. If a specific tender doesn't show these, it's
most likely genuine source coverage (SAM.gov is currently the only
source with a secondary email; several sources have no address
field at all — see migration 029's own coverage table), not a
missing feature.

### Report Intel layout + ROW_CAP removal (2026-09)

A user-reported layout bug in the Next-Best-Action engine card: rows
in the "See all N" modal forced horizontal scroll because the
`value` column used `white-space:nowrap` — fine for short values like
"87/100" (what most engines produce) but Next-Best-Action's own value
is a full recommendation sentence. `reportEngineRow` now gives any
value over 40 characters its own full-width, normally-wrapping line
under the label instead of the narrow nowrap column, the same layout
`sub`/`detail` already use.

**A real, separate bug found in the same investigation**: `ROW_CAP =
60` silently truncated what travelled with EVERY engine block, so "See
all 414" opened a modal that could only ever show the same 60 rows
already on the card — the exact dishonesty this module's own
docstring says it exists to prevent ("a headline next to 6 visible
rows is only honest if the rest are actually reachable"). Removed
entirely rather than raised; these rows are short strings and a large
report is still a modest JSON payload.

### TED contact extraction (2026-09) — the platform's biggest source gained real contact data

A user-reported "Tender Briefing shows no contact" turned out to be
real for TED-sourced tenders specifically (the feature already worked
correctly for SAM.gov, UK Find a Tender, ProZorro and Colombia — the
code was never broken, TED genuinely never requested these fields).
Investigated live rather than left as CLAUDE.md's previously-stated
gap ("EU TED: not attempted... risks breaking the single biggest
source without a live test to catch it first" — that live test is
what changed here). TED's search API returns its full ~1,830-field
list on any invalid `fields` request (a genuine, exploitable
documentation shortcut), which was used to find and confirm real
field names: `buyer-email`, `buyer-post-code`, `buyer-city`,
`organisation-street-buyer`. Live-sampled 5 real notices (one a
Ministerie van Defensie/Netherlands MoD tender) — all 5 had both
email and full street address populated, not sparse. `buyer-email`
and `organisation-email-buyer` return identical values, so only the
shorter name is requested. A bare country name with no street/city/
postcode is deliberately NOT shown as an "address" (not useful to
anyone), guarding `contact_address` on at least one real locality
part being present. First live re-ingestion: 607/607 of the run's
programmes got both fields — a 100% hit rate matching the sample, not
a coincidence.

Also fixed while investigating: both Tender Briefing and Manage panel
used to hide the entire "Procurement contact" section with no visible
acknowledgment when a tender genuinely had none published (most
common for TED before this fix, and still true for EU TED... wait,
now populated — remains true for any source/tender with genuinely
nothing published). Now shows an explicit "Not published by
{source} for this tender" note instead of silent absence, so a user
can tell "this feature doesn't apply here" apart from "this looks
broken."

### Horizontal-scroll layout pass (2026-09)

A user complaint ("kahi bhi left-right scroll nahi karna") that
started from the Report Intel modal extended to the whole app.
Addressed structurally, not just for the one table already fixed
above:
- Added `word-break:break-word` to `.datatable td` globally — a
  cheap, safe, broad fix for any long unbroken string (a URL, a long
  company name) that would otherwise force column width regardless of
  normal wrapping.
- The Opportunity Dashboard's main table had grown to 12 columns
  (Product, Programme, Source, Organization, Score, Confidence, Fit,
  Opened, Stage, Next Action, Eligibility, Manage) — no realistic
  screen fits that without scrolling regardless of text wrapping.
  Consolidated to 7: Source/buyer/country/eligibility now fold into
  the Programme cell as wrapping sub-lines (the same pattern
  `programmeStatusRemark` already used), and
  Score/Confidence/Fit merge into one "Signals" column. `table-
  layout:fixed` with percentage column widths plus `word-break`
  keeps it contained.
- The Ingestion Control table (9 columns: [ ], Source, Scheduled,
  Next Run, Last Run, Last Result, Records, [ ]) — the exact table
  shown in this platform's very first screenshot this session —
  consolidated to 5: Schedule (scheduled/not + next run time) and
  Last Run (timestamp + status + record count) each fold three
  columns into one wrapping cell.
- The top navigation bar's own horizontal scroll
  (`.topbar-row2{overflow-x:auto}`) was deliberately left as-is — a
  standard, recognisable "scrollable tab bar" pattern for ~20 modes
  that cannot all fit one row on any real screen, genuinely different
  in kind from the table layout bugs above. Left for the user to
  confirm they want that changed too, rather than assumed.

### SAM.gov pagination + rotation fixes (2026-09) — including a real self-correction

Investigating a user-reported real example (a specific UAS tender
whose SAM.gov page shows a name, email, phone and full office address
that this platform's Tender Briefing showed none of) led to several
real, verified findings — and one over-claim caught and corrected
before it could mislead anyone reading the code later.

**Confirmed real and kept**: `fetch_opportunities`'s `limit` was
raised from 100 to 1000 — confirmed live that SAM.gov genuinely
returns up to 1000 real records per call (not silently capped) at no
extra API-quota cost (still one request). A per-NAICS-code offset
rotation (`SAM_GOV_OFFSET_<code>`) was added using the same
`get_rotation_index`/`advance_rotation_index` machinery as the
existing NAICS-group rotation, so any code/window whose real total
exceeds 1000 gets rotated through in later runs rather than always
reading the same first page forever.

**A real, separate bug found and fixed while verifying the rotation
actually worked**: `advance_rotation_index` was a bare `UPDATE`, which
silently affects zero rows when no row exists yet for that
`source_name` — invisible because `get_rotation_index`'s own default
of 0 makes a first read look completely normal. CPPP's rotation key
happens to be pre-seeded by its own migration (037), which is why
this was never caught there, but SAM.gov's new per-NAICS-code keys
are dynamic (one per code ever queried, not a fixed known set) and
nothing seeds them. Fixed at the root — `advance_rotation_index` is
now a real `INSERT ... ON CONFLICT (source_name) DO UPDATE`, so every
rotation key self-seeds on first use. This fixes the bug for EVERY
current and future rotation key project-wide, not just SAM.gov's new
ones. Confirmed live: a row that provably did not exist before this
fix existed with the correct index after it.

**A genuine over-claim, caught and corrected, not left standing**:
while investigating, an initial manual verification queried SAM.gov
with `naicsCode=336411` as the parameter name and got back
`totalRecords: 32,597`, leading to a first-pass claim that this
project was reading "0.3% of available records" for that code. That
verification was wrong — `naicsCode` is the RESPONSE field name, not
a valid query parameter; SAM.gov silently ignores unrecognised
parameters rather than erroring, so that call was actually
unfiltered by NAICS at all. The correct query parameter, confirmed
against GSA's own official docs, is `ncode` — which this code has
used correctly since it was first written. Re-querying with the
correct parameter for the same code/window returned the true total:
139, not 32,597. The code comments that had already been written
with the wrong number were found and corrected in the same sitting,
before this summary was written, rather than left as a wrong claim
for someone to trust later.

**Resolved and re-verified live (2026-09-19), and the earlier
hypothesis here was wrong — corrected rather than left standing.**
Once SAM.gov's daily quota reset, the excluded-past-deadline
hypothesis above was directly tested: a `title=`-based search for this
exact tender returns it with BOTH the default `active` filter and
`active=false` explicitly set, and its own `responseDeadLine`
(2026-08-13, already passed) sits alongside `"active":"Yes"` in the
response — SAM.gov's search endpoint does NOT exclude past-deadline
notices the way the earlier hypothesis assumed. So the real reason
this record was missing was simply the same `limit=100`/no-rotation
gap already fixed above, not a second, deadline-based exclusion.
Confirmed directly against this platform's own database
(`select ... from programmes where name ilike '%Aerial Ignition
Capabilities%'`): the record now carries the real, correct data —
`contact_name: Andre Bishop`, `contact_email:
andre.r.bishop.civ@army.mil`, `contact_address: FORT DRUM, NY,
13602-5220, USA` — picked up by a routine scheduled run after the
`limit`/rotation fix, with no special backfill needed.

### Sector Coverage — real per-sector tender/opportunity counts (2026-09)

A user-requested feature: for each of the platform's capability
sectors, show how many real ingested programmes fall in it and how
many of the tenant's own opportunities converted from it, live on
hover, with click-through to the actual list.

`app/sector_coverage.py` reuses `app/capability_resolver.py`'s
code -> capability resolution — the exact same mechanism Customer/OEM
Intelligence already use — so a programme's sector here can never
drift from what those modules would say about the same programme; a
new, independent "what sector is this" definition was deliberately
NOT written. `programme_count` is shared/global (every tenant sees
the same real platform data); `opportunity_count` is scoped to the
caller's own tenant via the same RLS-enforced session every other
tenant-owned-table query already relies on — no manual tenant_id
filter needed, `opportunities` already carries that boundary.

**A real, structural bug found and fixed while building this**: the
frontend's hardcoded 20-sector list (`renderIndustries`, and a
second, differently-abbreviated copy in the sidebar) was missing
"Personal Equipment" — the sector this session's own
TACTICAL.PROTECTIVE_EQUIPMENT capability (migration 033) added,
never back-filled into either hardcoded list. Both now read from one
shared `INDUSTRY_SECTORS` array instead of two independently-drifting
copies.

**A second, more serious bug found while sanity-checking the real
counts against the database directly, not assumed clean**:
`capability_taxonomy` (shared, non-tenant-scoped reference data)
contained 9 permanent junk rows with `sector = 'Test'` / `'Test
Sector'` — literal test fixture data from `test_taxonomy_admin.py`'s
own `test_platform_admin_can_create_taxonomy_entry_and_add_keywords`
and `test_duplicate_taxonomy_code_is_rejected`, which create a real
row via the live API on every test run and never clean it up
afterward (confirmed: running the test suite once added the junk
rows right back after a one-time manual cleanup). This would have
shown "Test" and "Test Sector" as two fake sectors on a page whose
entire point is showing real market coverage. Fixed at the root —
both tests now delete the row they created via the `db_cursor`
fixture they already had available, not just cleaned up once by
hand. Verified: running the full test suite no longer leaves any
`sector IN ('Test', 'Test Sector')` rows behind.

Frontend: `renderIndustries` renders all 21 sector cards immediately,
then `loadIndustriesSectorCoverage` fetches `GET
/market/sector-coverage` and fills in each card's live counts (a
real "0" for a sector with a mapped capability but no ingested
programmes yet, never silently hidden). Clicking a card opens a modal
of the tenant's own opportunities in that sector, reusing `GET
/opportunities`'s newly-added `sectors` field (computed the same way,
attached to every opportunity row so the click-through never needs a
second resolution pass or a dedicated filter endpoint). The
"Industries" page was moved into `PRIVATE_MODES` (login-gated) since
`opportunity_count` is genuinely per-tenant data, matching how every
other real-data page in this app already works.

### A real taxonomy data-quality bug, found by a user reading the Sector Coverage numbers (2026-09, migration 041)

The user checking the just-built Sector Coverage feature noticed the
exact same count (994) under seven unrelated sectors — ISR, Naval
Systems, Electronic Warfare, C-UAS, C4ISR, Sensors, Radar — and asked
for it to be verified rather than accepted. It was real, and it was a
genuine bug, not a display issue: 994 of that tenant's matched
programmes all carry the SAME NAICS code (334511), and that one code
had been mapped to **11 different capabilities across 11 different
sectors** since migration 004 (this project's original "curated v1
starting set, not exhaustive"). Migration 031 (this session) even
added a 12th mapping on top of it (SONAR.PASSIVE), reasoning at the
time that "everyone else already accepts this imprecision" — a
rationalization that, once a real user surfaced the actual
consequence, did not hold up and should have been caught then.

Checked against the OFFICIAL US Census NAICS 334511 definition
("Search, Detection, Navigation, Guidance, Aeronautical, and Nautical
System and Instrument Manufacturing" — explicitly: aircraft
instruments, flight recorders, navigational instruments and systems,
RADAR systems and equipment, SONAR systems and equipment) rather than
guessed at. Three of the eleven mappings are genuinely grounded in
that text — SENSING.RADAR, SONAR.PASSIVE, SENSORS.GENERAL — and eight
are not: UAV.INTEGRATION, EW.GENERAL, C4ISR.INTEGRATION,
SENSOR.ELECTRO_OPTIC, AUTONOMY.GENERAL, CUAS.GENERAL, ISR.GENERAL,
AUTONOMY.ROBOTICS. Migration 041 deletes only those eight, keeping the
three the definition actually supports. Live-reverified against the
same real tenant afterward: the 994 now shows correctly on only Radar/
Naval Systems/Sensors; the other seven sectors correctly dropped to
whatever their OWN genuinely-mapped codes contribute (0-207 range, no
more inflated shared numbers).

**Deliberately NOT applied to NAICS 541712** (Research and Development
in the Physical, Engineering and Life Sciences), the platform's other
multi-capability code (6 capabilities: EW, Cyber, C4ISR, AI/Autonomy,
ISR, Robotics) — checked and found genuinely different in kind: 541712
classifies the ACTIVITY of doing R&D, not WHAT is being researched, so
NAICS alone cannot narrow it further no matter how carefully it's
read. That breadth is a real, accepted limit of the classification
scheme itself, not a seeding mistake, and was left untouched.

**Real, wide test-suite impact, fixed rather than worked around**: a
"UAV-classified product + NAICS 334511 fixture programme" pattern had
been copy-pasted across seven integration test files (relying on the
very mapping this migration removes). Each was switched to NAICS
336411, which genuinely maps to UAV.INTEGRATION and was already
correct — not a new guess, an existing correct mapping the tests
simply weren't using. `test_matching_scoring_unit.py`'s own mention of
334511 is a pure-logic unit test with no DB, and `test_sam_gov_live.py`/
`test_rate_limiting.py`'s mentions test ingestion/rate-limiting
behavior, not capability resolution — both correctly left untouched
after checking, not assumed safe.

### A real false-positive match, user-reported: "UAV Kumbhigram" is a place, not a drone (2026-09)

A user spotted a CPPP (India) street-light-repair tender scoring
92%/high for a UAV product. Root cause, confirmed live: the title
contains "...UAV KUMBHIGRAM..." — a real Indian military station name
in Assam, not a reference to unmanned aerial vehicles — but the bare
3-letter keyword `"uav"` (weight 3, `UAV.INTEGRATION`) still matched
it at a genuine word boundary (`\buav\b`, `app/scoring.py`'s own
matching regex, working exactly as designed). Because the programme's
`naics_code` was `IN-DEF` (the India organisation sentinel — see
`ORGANISATION_SENTINEL_CODES`), it had ZERO real classification-code
evidence to begin with; that one short-token keyword match was the
ONLY thing standing between "genuinely relevant" and "coincidental
collision with an unrelated place name."

Fixed in `programme_matching.py`: for a sentinel-only row
specifically (not for any code-matched programme, where a short
keyword still legitimately adds confidence on top of already-real
category evidence), the matched keyword(s) corroborating it must
include at least one that is either 5+ characters or a multi-word
phrase (contains a space) — `"uav"`/`"uas"`/`"vtol"`/`"dron"` no
longer qualify as the SOLE evidence for a sentinel match, while
`"drone"`, `"quadcopter"`, and phrases like `"unmanned aerial"` still
do, since a longer word or real phrase is far less likely to collide
with an unrelated place/unit name by coincidence than a bare 3-4
letter acronym is. Live-reverified: re-running the real match for the
product that surfaced this bug now returns 0 matches for the
KUMBHIGRAM tender (was 1, score 6/high) out of 802 total matches: 3
new integration tests pin both directions (the false positive
excluded, a genuine multi-word or 5+-character keyword match still
included) — `test_sentinel_keyword_corroboration.py`.

**A real, separate finding while investigating, not auto-fixed
here**: re-matching a product only ever INSERTs new opportunities or
UPDATEs existing ones — it never DELETES an opportunity that no
longer qualifies after a scoring-rule change, so the specific reported
opportunity had to be deleted by hand (confirmed as a genuine false
positive first) rather than disappearing on its own once the fix
shipped. Checked the wider scale before deciding what to do about it:
**523 sentinel-sourced (IN-DEF/ZA-DEF) opportunities exist platform-
wide** — not all necessarily wrong (many likely have real multi-word
corroboration), but a proper audit of all 523 against the corrected
rule was NOT attempted here, since safely doing so means
reconstructing which capability each opportunity was originally
matched under (not directly stored on the row) and re-running the
corrected logic per one — a real, scoped follow-up worth doing
deliberately, not a blind bulk DELETE that risks destroying genuinely
valid matches if that reconstruction has any subtle bug. Left as a
stated, honest gap rather than either ignored or risked.

### Tender Briefing "open on source" link (`ui_link`) — audited across all 10 sources (2026-09)

A user reported that clicking "open" on a CPPP tender in Tender
Briefing showed "invalid URL," and explicitly asked to check every
source, not just CPPP. A live audit query across all 10 sources'
`programmes.ui_link` null/malformed rate found two genuine, different
problems and confirmed the rest were either correct-by-design or
stale data, not bugs:

- **Real bug, fixed**: `GET /opportunities` (the list route used by
  the Manage panel's grid and several drill-down modals) never
  selected `pr.ui_link` at all — `PATCH /opportunities/{id}` (the
  single-opportunity route Tender Briefing's own "Open" button
  actually calls) already had it, so the exact modal-click path the
  user tested could not reproduce their report directly, but any
  other UI surface reading the list route's `ui_link` field was
  silently getting `undefined`. Fixed by adding `pr.ui_link` to the
  list route's SELECT.
- **Real gap, fixed**: DNCP Paraguay never had a `ui_link` field at
  all (100% null, 92/92) — not a bug, a feature that was simply never
  built for this source, unlike AusTender's below. Found a working
  URL-reconstruction pattern live: DNCP's own ocid format is
  `ocds-03ad3f-{id_llamado}-{n}`, where the middle segment is the
  portal's own call ID — confirmed by extracting it from a real
  ingested ocid and loading
  `https://www.contrataciones.gov.py/datos/visualizaciones/ciclo_licitacion/index.html?id_llamado={id}`,
  which returned a genuine HTTP 200 with real licitación-cycle
  content, not an error page. Wired into `paraguay_normalize.py`
  (`_ui_link_from_ocid`, gated behind a regex so a malformed ocid
  shape degrades to no link rather than a guessed-wrong one) and
  `paraguay_ingestion.py`'s upsert. Live-reingested after shipping:
  44/44 freshly-touched rows now carry a real, working link.
- **AusTender's 100% null rate (1261/1261) is correct, not a bug** —
  already documented in code (`"AusTender's OCDS API publishes no
  public notice URL field"`). A candidate URL-reconstruction guess
  (`https://www.tenders.gov.au/Cn/Show/{id}`, based on real examples
  found via search) was live-tested against our own real ingested
  data and returned a genuine "Not Found" page — caught before
  shipping, no code change made.
- **SAM.gov, TED, CanadaBuys, UK Find a Tender, and CPPP's remaining
  partial null rates are real per-record upstream gaps, not stale
  data or bugs**: confirmed each source's `ui_link` is already
  correctly computed on every ingestion run and already correctly
  persisted with `ui_link = excluded.ui_link` on every UPSERT (not a
  one-time INSERT) — the null rows' own `last_updated` timestamps are
  as recent as freshly-touched non-null rows from the SAME ingestion
  run, ruling out "just needs a re-run." Each source already has a
  deliberate, documented "build the link only when the ID/shape we'd
  need is actually present, else `None`" fallback (e.g. CanadaBuys'
  `canadabuys_portal_url()` only fires for a verified slug shape) —
  the same graceful-absence discipline as AusTender's own decision
  above, not something to guess around. CPPP's nulls specifically
  come from rows whose listing-page title cell had no `<a href>` at
  scrape time; a live sample of the current listing found no such
  no-link rows, consistent with those specific tenders having since
  closed/expired rather than a live scraper defect.

### Two eligibility follow-ups: the 523/757-row sentinel audit, and UK FT's real reserved-participation field (2026-09-23)

Two items explicitly left open by earlier sessions, closed out here.

**1. The 523 (now 757) sentinel-sourced opportunities, audited —
found to be 100% test/dev debris, not real customer exposure.** The
UAV Kumbhigram fix (see above) flagged 523 platform-wide IN-DEF/
ZA-DEF-sentinel opportunities as an unaudited risk. Before writing any
cleanup logic, checked WHO actually owns them:

```sql
select case when t.name like 'Test Company%' then 'pytest-generated' else t.name end,
       count(distinct t.id), count(*)
from opportunities o join programmes p on p.id=o.programme_id join tenants t on t.id=o.tenant_id
where p.naics_code in ('IN-DEF','ZA-DEF') group by 1 order by 3 desc;
```

Result: 755 of 757 belong to 551 tenants named `Test Company <hex>` —
pytest's own `unique_email()`/`new_tenant` fixture signing up a fresh
throwaway tenant on every single test run, accumulated across this
session's many full-suite runs against the shared dev DB. The
remaining 2 belong to `Report Co`, a manually-created dev fixture from
earlier Report Intel work — also not a customer. **The real production
tenant (`Syas Ai and Automation`) has ZERO sentinel-sourced
opportunities right now** — confirmed by also checking its full
opportunity-by-source breakdown (1362 SAM.gov + 412 CanadaBuys + 92
TED, zero CPPP/South-Africa entries at all), consistent with the
earlier `ui_link` investigation's own finding that the real tenant
currently has no live CPPP matches. No cleanup code was written,
since there is nothing real to clean — the correct audit result here
is "no customer-facing risk exists," not a bulk DELETE. Separately
worth noting: this project's test suite runs against the same dev DB
named in `DATABASE_URL` rather than an isolated test DB (see this
file's own Tests section) — repeated full-suite runs are the actual
source of the 552 throwaway tenants now in this DB, a real,
accumulating side effect of that setup worth keeping in mind, not
something this pass changed.

**2. UK Find a Tender's reserved-participation field, wired — but the
field NAME an earlier session recorded was wrong.** The Eligibility
Phase 1 audit above named the field `tender.otherRequirements.
reservedParticipationLocation` with a Nottinghamshire example. A much
wider live re-check this session (1900 real releases, all three
stages — planning/tender/award — across a full year, not a small
sample) found ZERO occurrences of that field name anywhere. The field
that actually exists is `tender.otherRequirements.
reservedParticipation` (no "Location") — an array of OCDS codelist
values, not a geographic string. One real example found live: release
`041633-2026` carries `["shelteredWorkshop"]` (a health-services
notice, CPV 85xxxxxx — not itself defense-relevant, but proof the
field and value shape are real). Wired into `uk_ft_normalize.py`
(`extract_set_aside`, reading the first array value) and `uk_ft_
ingestion.py`'s upsert, mirroring the same `set_aside_code`/
`set_aside_description` shape every other wired source already uses.
Only the one live-observed code (`shelteredWorkshop`) gets a
translated description; any other real OCDS code this project hasn't
directly seen is shown as its raw value rather than a guessed-at
label — the same "don't invent, surface the raw source text" rule
DNCP Paraguay's own eligibility fallback established. 5 new unit
tests added (`test_uk_ft_normalize_unit.py`). Live-reingested after
shipping: ran cleanly, 0 of the 29 currently-ingested real UK FT
programmes carry a set-aside value yet — an honest negative result,
the same "genuinely never observed live" situation Paraguay's own
eligibility field was in before its first real restricted example
showed up, not a sign the wiring is broken.

### Eligibility / Export-Control — Phase 1 (2026-09)

The highest business-risk item from a security-controls review,
built to the same conservative plan discussed with the user
beforehand: this is descriptive-only, and DELIBERATELY never
determines actual bidder eligibility — same "not legal advice,
confirm with export-control/trade-compliance counsel" stance already
established for `app/fit_score.py`'s restriction gate. The plan's own
Phase 2 (a real automated eligibility verdict) was explicitly
recommended AGAINST staying permanent-descriptive-only, not a
stepping stone to build later — see the plan itself for the full
reasoning (liability risk of a wrong automated "you can/can't bid"
signal).

**TED confirmed to publish real, structured bidder-restriction
fields — the first source beyond SAM.gov's set-aside** (`set_aside_
code`/`set_aside_description` had been SAM.gov-only since those
columns were added; every other source left them null). Found live,
not guessed: TED's full ~1830-field list (the same "request an
invalid field to dump the real list" trick already used for TED
contact fields) was searched for eligibility-shaped names, then the
candidates were verified against 10 real defence-relevant notices
before wiring anything in.

- `sme-lot` — a plain boolean per lot, confirmed populated (`true` on
  2 of 10 sampled notices) — SME-reserved is a real bidder
  restriction, the same kind of fact SAM.gov's set-aside already
  surfaces. Checked first (before reserved-procurement-lot) when both
  are present, since "reserved for SMEs" is the clearer, more
  specific fact.
- `reserved-procurement-lot` — confirmed to genuinely publish `"none"`
  on every sampled notice when unrestricted (a real, structured
  "not restricted" signal, not an absent field) — any other value is
  surfaced as-is, since a real non-`"none"` example was never
  actually observed live and this project's own standing rule is to
  show what a source states rather than translate a category it
  hasn't verified the wording of.

Wired into `ted_eu_normalize.py`/`ted_eu_ingestion.py` using the
existing `set_aside_code`/`set_aside_description` columns — the same
representation SAM.gov already uses, so every downstream consumer
(Fit Score's restriction gate, Tender Briefing's Eligibility row, the
Opportunity Dashboard's badge) picked this up for free, no new field
plumbing needed anywhere else. Live-verified after a real re-
ingestion run: 168 of 1,000 freshly-ingested TED programmes (16.8%)
now carry real SME-restriction data that was previously always null.
30/30 TED normalize unit tests pass (6 new).

**Frontend**: Tender Briefing's Eligibility row gained two things it
didn't have — an explicit "Your company: Registered in `<country>`"
line right next to the tender's own stated restriction (pure
juxtaposition of two independent facts, no verdict computed from
them), and the same "Not legal advice" disclaimer wording
`fit_score.py`'s panel already carries, so the two can't drift into
different tones on the same question.

### Eligibility / Export-Control — Phase 1, all 10 sources audited (2026-09)

Direct follow-up to the TED-only pass above, on the user's own
explicit request to check the remaining 8. Same discipline throughout
— find the real field or column live, sample real data, confirm
before wiring anything in, and record a genuine negative result as
honestly as a genuine positive one rather than leaving a source
unchecked.

**Three more real, wired, live-verified restriction signals found**:

- **CanadaBuys** — `limitedTenderingReason-raisonAppelOffresLimite-eng`,
  a real CSV column, confirmed populated on 307 of 905 real open
  tenders (34%) in a live pull, with exactly 3 distinct real values:
  `"None"` (open competition), `"Exclusive Rights"` (only one specific
  supplier eligible), and `"No response to bid solicitation"` (open
  competition already failed, buyer now contacting known suppliers
  directly — also genuinely not open to a general bidder). Any value
  other than `"None"` is surfaced as a stated restriction. Wired into
  `canada_buys_normalize.py`/`canada_buys_ingestion.py`
  (`COL_LIMITED_TENDERING_REASON`). Live-reverified after a fresh
  ingestion run: 6 real tenders now carry `"Exclusive Rights"`.
- **DNCP Paraguay** — `tender.eligibilityCriteria`, a free-text
  Spanish field at the exact same
  `records[0].compiledRelease.tender` path this source's own
  classification-code extraction already reads (`extract_
  classification_from_detail`) — no second detail fetch needed,
  `extract_eligibility_from_detail` is a sibling read of the same
  payload. `fetch_classification_codes` in `paraguay_ingestion.py`
  now returns a dict-of-dicts (code + eligibility together)
  specifically so a caller can never end up with the two facts
  computed from two different fetch attempts of the same ocid.

  **A real bug, caught by the FIRST genuine live re-ingestion run
  (2026-09-22), not assumed away from the small initial sample**: the
  first version only checked for `"ninguna"` as "not restricted", on
  the strength of one sampled value. The real, full re-ingestion
  surfaced two more real phrasings being wrongly surfaced as stated
  restrictions — `"Restricciones: NO APLICA"` ("N/A"), and standard
  Paraguayan legal boilerplate (`"Podran participar todos los
  oferentes que cumplan con los requisitos Tecnicos, legales y
  economicos... Articulo 21 de la Ley 7021/2022..."` — "all bidders
  who meet the normal legal/technical/economic requirements may
  participate", citing the general eligibility law everyone must meet,
  not a restriction to a specific bidder category). Also found live:
  the identical boilerplate text appears BOTH with and without Spanish
  accents across different real rows in the same run. Fixed by
  checking via `_fold` (this module's own existing accent-folding
  helper, already used for buyer-name matching) for `"NINGUNA"`,
  `"NO APLICA"`, and `"TODOS LOS OFERENTES"` — a second real bug
  caught in the same fix, before it shipped: `_fold` returns
  UPPERCASE (per its own definition), and the first draft of this fix
  compared against lowercase phrases, which would never have matched
  anything. Caught by testing the fix itself, not just trusting it
  compiled.
- **UK Find a Tender** — flagged in this pass as `tender.
  otherRequirements.reservedParticipationLocation`, "confirmed real
  and populated live" with a Nottinghamshire example. **That field
  name was wrong — corrected in the 2026-09-23 follow-up below.** A
  much wider re-check (1900 real releases, all three stages, a full
  year) found zero occurrences of `reservedParticipationLocation`
  anywhere; the real field is `tender.otherRequirements.
  reservedParticipation` (no "Location"), an array of OCDS codelist
  values, not a geographic string — see below for what actually got
  wired. Left here rather than deleted, as an honest record of a
  wrong finding, not silently corrected away.
  Deliberately did NOT wire `tender.lots[].suitability.sme` even
  though it was also found live and populated — that field means "SME
  bidders are welcome/suitable," the OPPOSITE direction of meaning
  from a restriction (TED's `sme-lot` means "RESERVED for SMEs only");
  treating it as a set-aside would have overclaimed a restriction that
  isn't actually stated.

**Four sources genuinely checked and confirmed to have NO eligibility-
shaped field** (a real negative result, not "not checked"):
eTenders South Africa (24 real OCDS releases sampled — this matches
the source's own already-documented thinness, e.g. `procuringEntity`
carrying only `id`/`name`), AusTender (100 real releases sampled —
consistent with its own documented nature as a CONTRACT-AWARD-ONLY
endpoint, where bidder eligibility criteria genuinely wouldn't apply
since bidding has already closed), SECOP II Colombia (the real
`modalidad_de_contratacion` column is a procurement METHOD type —
open competition vs. direct contracting vs. low-value threshold — not
a bidder-eligibility CATEGORY restriction, a real distinction, not
hair-splitting: a "direct contracting" tender isn't necessarily
restricted to a specific eligible bidder class, the buyer simply
skipped open competition), and CPPP India (the listing page this
source scrapes has a fixed 6-column table with no eligibility column
at all — the field, if any, would only exist on each tender's
individual detail page, which this source does not currently fetch;
doing so would be a genuinely bigger architecture change, the same
two-phase shape ProZorro/Paraguay already use, not attempted here).

**One source checked with an inconclusive result, deliberately NOT
wired**: ProZorro's real schema does carry `restricted` and
`hasPrequalification` keys (confirmed present in the raw JSON), but
all 3 live-sampled real tenders had both as `null` — never observed
actually populated, so nothing was wired rather than guessing at what
a real non-null value would mean.

**Net result at the time: 4 of 10 sources had a real, wired
eligibility signal** (SAM.gov's set-aside, already real before this
session; TED; CanadaBuys; DNCP Paraguay), 1 more found and verified
but not yet wired (UK Find a Tender), 4 confirmed to genuinely have
nothing to wire, and 1 inconclusive. **UK Find a Tender was wired in
a 2026-09-23 follow-up (see above, and the MFA entry below) — 5 of 10
now wired.** This is the honest, current state of Phase 1's
eligibility-coverage goal — not "10 of 10 sources covered," which
would overclaim what several of them can actually publish.

### MFA (TOTP) — the second security-controls item (2026-09-23, migration 045)

Recommended alongside Eligibility/Export-Control Phase 1 back when
that work started; the user chose Eligibility first, then asked for
MFA once the sentinel-opportunity audit and UK FT eligibility
follow-ups were done. Login was password-only until this — one
compromised password gave full access to a tenant's tender/pricing
data, nothing else in the way.

**Design**: TOTP (RFC 6238, `pyotp`), three deliberately separate
steps rather than two:
1. `POST /auth/mfa/setup` generates and stores an ENCRYPTED secret,
   but `mfa_enabled` stays false — an unconfirmed secret must never
   gate a user's own login, or a broken authenticator-app scan would
   lock someone out of their own account before they'd even proven
   the setup worked.
2. `POST /auth/mfa/enable` requires one real code from that secret
   before flipping `mfa_enabled` true — only then are 10 backup codes
   generated and returned (plaintext, once, same "shown once, hash
   forever after" discipline as a password-reset token). No point
   handing out recovery codes for a factor never proven to work.
3. `POST /auth/mfa/disable` requires re-entering the current
   password — removing a security factor is itself a security-
   lowering action, gated the same way a session merely staying open
   isn't enough to bypass.

Login flow: `POST /auth/login` now returns EITHER a real
`access_token` (no MFA) OR `mfa_required: true` plus a short-lived
(5-minute) `mfa_challenge_token` carrying a `purpose` claim a normal
access token doesn't have, so it can never be used as a real token
even sent directly to an authenticated route. `POST /auth/login/mfa`
takes that challenge token plus either a real TOTP code or a backup
code, and only THEN issues the real access token. A used backup code
is removed from the stored array (not flagged) — verified live in
`test_a_used_backup_code_removed_from_the_list_no_longer_verifies`
and the flow-level replay test in `test_mfa_flow.py`.

**Encryption**: `mfa_secret` is Fernet-encrypted at rest under a
DEDICATED `MFA_ENCRYPTION_KEY`, deliberately separate from
`BACKUP_ENCRYPTION_KEY` (app/backup.py) — reusing one secret for two
unrelated purposes means a leak of either compromises both; a real
key was generated and added to this project's own `.env` the same
way `BACKUP_ENCRYPTION_KEY` was. `mfa_backup_codes` stores only
SHA-256 hashes, never the plaintext codes, and the backup-code
alphabet (`app/mfa.py`'s `BACKUP_CODE_ALPHABET`) deliberately excludes
`0/O/1/I/L` — a backup code is meant to be read and typed by a human
locked out of their own account, not just machine-generated.

**A real, reproducible bug found in this project's OWN migration
tooling while applying migration 045, not in the migration's SQL
itself**: `alembic upgrade head` cannot execute this file — confirmed
live that both a single `op.execute()` of the whole file
("`cannot insert multiple commands into a prepared statement`",
asyncpg's prepared-statement protocol refusing multiple top-level SQL
commands in one prepare call) AND a split into one `op.execute()`/
`exec_driver_sql()` call per statement (a DIFFERENT crash, deep
inside SQLAlchemy's own asyncpg error-translation code —
`TypeError: expected string or bytes-like object, got 'NoneType'`)
both failed. **The SQL itself is fine** — confirmed by applying the
exact same file cleanly via `psql -f` (`ALTER TABLE` + 3×`COMMENT`,
all succeeded). **Correction (2026-09-23, found while building the
isolated test stack below): this was NOT the first migration to hit
this** — `0001_baseline` itself (roles.sql + schema.sql, far more
than 2 statements) fails the exact same way on a genuinely fresh
database, and roughly a third of all 45 migrations have more than 2
top-level statements. This project's real dev DB simply never
exercised a truly fresh `alembic upgrade head` run before, since it
has been stamped-forward from `0001` for months rather than
bootstrapped from empty — a systemic gap, not a `0045`-specific one;
see the "Isolated test stack" entry further up this file for the real
fix (apply every migration's raw `.sql` via `psql`, then `alembic
stamp head`, for ANY fresh database, not a per-migration workaround).
Applied to the real dev DB with the narrower fallback at the time
(direct `psql` + `alembic stamp 0045_mfa`) — see migration 0045's own
`upgrade()` docstring for that detail. **A real gap in "fresh DB:
`alembic upgrade head`" from this file's own Running-it-locally
section** — worth knowing before trusting a clean `alembic upgrade
head` run on a brand-new
database if a future migration also has 3+ top-level statements.

**Live-verified end to end** (not just unit-tested): a real signup,
real `/auth/mfa/setup` call, a real `pyotp`-generated code against the
real returned secret, real `/auth/mfa/enable`, a real subsequent
`/auth/login` correctly withholding the access token and returning
`mfa_required`, and a real `/auth/login/mfa` completing with that
same real code. 17 unit tests (`test_mfa_unit.py`, pure logic — no
DB/network) + 16 integration tests (`test_mfa_flow.py`, real HTTP
against the real running stack) — all passing.

**Not done in this pass, worth stating rather than leaving implicit**
(the frontend UI gap below was closed the same day — see the next
entry): no "remember this device for 30 days" convenience (every
login re-checks the second factor); no admin ability to force-reset
another user's MFA if they lose both their authenticator and all 10
backup codes (today that's a manual DB operation, same tier of action
as any other direct database fix in this project).

### MFA frontend UI + two password-management follow-ups (2026-09-23)

Built the same day as the MFA backend above, once the user asked for
the actual screens.

**MFA UI**, all in the single-file frontend
(`defence-opportunity-intelligence-app-v12.html`): a "Two-Factor
Authentication" panel on the Company Profile page (the only existing
per-account settings surface — MFA is genuinely per-USER, not per-
company, so the panel says so explicitly rather than implying it's
shared). A bespoke modal (`setMfaModalStep`/`closeMfaModal`), NOT a
reuse of the existing `showInfoModal` helper — that helper always
closes on its Continue button before running its callback, which
can't stay open to show an inline "wrong code" error the way a setup
step needs to. Three steps: setup key (copyable, no QR — this app has
zero external script/CDN dependencies by design, including no QR
library, so manual key entry is the only path; every real
authenticator app supports it) → 6-digit verify → 10 backup codes
behind a mandatory "I've saved these" checkbox before "Done" enables.
Login: `/auth/login` returning `mfa_required` now shows a dedicated
second-factor screen (`renderMfaLoginScreen`) with a TOTP/backup-code
toggle, sharing one `finishLogin()` helper with the normal password-
only path so "where do I land after signing in" logic lives in
exactly one place, not two copies that could drift.

**Verification, honestly caveated**: no browser or headless-Chromium
tooling exists in this dev container (checked: no `chromium-cli`, no
Linux-native npm/playwright, no Windows-Chrome bridge that works from
here) — this was NOT visually click-tested end to end the way the
backend was. What WAS done: full JS syntax validation of every
`<script>` block, an automated cross-check that every
`getElementById()` call in the new code matches a real `id="..."` in
the HTML it renders (none missing), and a field-by-field match of
every JSON key the frontend reads against the actual backend response
models. The backend side of the exact same contract was already
live-verified with real `pyotp` codes before this UI was written. The
user was told directly that this gap exists and asked to test it
themselves in a real browser.

**Two related follow-ups, asked in the same message**: "reset password
option account ke andar bhi do" (forgot-password needs real SMTP,
which isn't configured) and "platform admin should have the privilege
to reset another tenant's password too — what do you think?"

- **`POST /auth/change-password`** — a plain logged-in self-service
  change, requiring the CURRENT password (same re-auth-for-a-security-
  action discipline as `/auth/mfa/disable`). The Company Profile page
  gets a matching form.
- **`POST /platform-admin/users/reset-password`** — the user's own
  question was answered with a recommendation, not just built as
  asked: a platform admin can trigger a reset LINK for any tenant's
  user by email, but the route deliberately does NOT let the admin
  set or see the actual new password — it reuses the exact same
  `password_reset_tokens` mechanism `/auth/forgot-password` already
  uses, just admin-triggered instead of self-serve, so a compromised
  or careless platform-admin account can unlock a user but can never
  learn their password (a real, separate risk — password reuse across
  sites is common, and an operator who can just set anyone's password
  is a much easier support-abuse vector to get wrong). Initially a
  small free-text-email panel on the Taxonomy Admin page — moved to
  its own "👤 User Management" tab the same day (see the next entry)
  once the user asked to search-and-select instead of typing an email
  from memory.
- 6 new integration tests (`test_password_management.py`) — including
  a full real chain proving the actual security property: platform
  admin triggers a link for a SECOND tenant's account, that link is
  redeemed through the normal `/auth/reset-password` route, the old
  password stops working and the new one (chosen by the target user,
  never seen by the admin) works.

### CORS bug: `file://` pages were silently blocked — a real, user-reported login failure (2026-09-23)

The user reported the frontend showing "Failed to fetch" on login,
and said resetting the password didn't fix it either — a strong tell
that NO request was reaching the API at all (a real bad-password 401
would come back FROM the server; "Failed to fetch" is the browser
refusing to even hand back a response). Root cause: this project's
single-file frontend is normally opened directly from disk
(double-click, no local server), which makes the browser send
`Origin: null` — and `CORSMiddleware`'s `allow_origins` only listed
`FRONTEND_ORIGIN` (`http://localhost:5501` in this dev `.env`) and
`http://127.0.0.1:5500`, neither of which is `null`, so the browser
silently blocked every single response, including the password-reset
submission itself. Fixed by adding the literal string `"null"` to
`allow_origins` (`api/app/main.py`) — not a wildcard, the actual
`Origin` value a `file://` page sends. Live-confirmed before/after
with `curl -H "Origin: null"` against `/auth/login`, checking for
`access-control-allow-origin: null` in the response; existing CORS
tests (`test_cors_and_auth_me.py`) still pass unchanged.

### User Management tab — search-and-reset instead of type-an-email (2026-09-23)

The platform-admin password-reset panel (previous entry) started as
a free-text email field on the Taxonomy Admin page. The user pointed
out the obvious next step: why make an admin already know someone's
exact email, when the platform could just list accounts to pick from?
Built `GET /platform-admin/users?search=&limit=` (`main.py`) — lists
real accounts across EVERY tenant, joined with role and MFA status,
defaulting to excluding pytest's own throwaway `Test Company <hex>`
tenants (the exact same naming convention the 2026-09 cleanup of 8155
of them was keyed on) so this dev database's own test-run debris
doesn't drown out real accounts; an explicit search still reaches
them if genuinely needed. Moved off Taxonomy Admin onto its own new
"👤 User Management" tab (`adminOnly` in `MODES`, same
`is_platform_admin` gate) with a debounced search box and a real
table — clicking "Reset Password" on any row calls the existing
`/platform-admin/users/reset-password` route with that row's email,
so the admin never types one by hand. Live-verified against the real
dev database (not just unit-tested): a fresh platform-admin test
account listing 50 real accounts by default, and a targeted search
for `syasaiandautomation` correctly returning exactly the user's own
real production account and no one else's. 3 new integration tests
added to `test_password_management.py` (role-gating, a search
correctly finding one specific freshly-created account, and the
default listing provably excluding every pytest fixture tenant).

**Moved again the same day**, off its own top-nav tab entirely: the
user asked for it to live at the BOTTOM of the Company Profile page
instead, stating explicitly why — per-user activity-monitoring logs
are planned to live there too later, and both are "manage a user,"
not "a page of its own." `renderUserManagement()` became
`renderUserManagementPanel()` (returns just the panel's inner HTML,
no page chrome, no standalone "Access Restricted" gate — the
CONTAINING panel in `renderCompanyAdmin()`'s template is itself only
emitted at all when `currentUser?.is_platform_admin`, so a non-
platform-admin's Company Profile page never even has a `#cp-users`
element in the DOM). `initUserManagementHandlers()` now runs from
inside `initCompanyAdminHandlers()` rather than its own
`postRenderHooks()` branch — both were removed from `MODES`,
`PRIVATE_MODES`, and the render dispatch table.

### The 1383 "who are all these accounts" cleanup, and the isolated test stack it led to (2026-09-23)

The user, looking at the new User Management table, asked directly
whether all those accounts were real. They were not — the same
test-suite-pollution problem the earlier 8155-tenant cleanup had
already found once, recurring, because nothing had actually stopped
new test runs from writing into the real dev DB. Before deleting
anything this time, the two accounts with real data were checked
individually rather than assumed: `Syas Ai and Automation` (1866
opportunities) and `M/s Alpha_Elsec defence and Aerospace Systems Pvt
Ltd` (364 opportunities, `Santosh.kumar@alpha-elsec.com`). The user
initially said to delete the Alpha_Elsec one too as unneeded — then,
told the FULL remaining-account list was about to be wiped, said they
had only ever created two accounts (Syas and one for
`santosh.umar@alpha-elsec.com`) and asked where the rest came from.
**Caught before running**: that second account WAS the one they meant
(a typo in the email they typed from memory:
`Santosh.kumar@alpha-elsec.com` is the real address) — kept, not
deleted. The other 1383 (down to exactly 2 tenants remaining) were
confirmed as pytest's own throwaway signups before deleting them,
same ordered transaction (null `evidence.reviewed_by`, delete
`audit_log`/`opportunities`/`products`, delete `tenants` last —
cascades `users`) the first 8155-tenant cleanup already used.

**The actual fix, not just another cleanup**: `docker-compose.test.yml`
— a genuinely separate Postgres + API pair (ports 5433/8001, own
Docker volume) that `tests/conftest.py` now hits by default instead
of the real dev stack, so a pytest run can no longer write a single
row into real data, structurally, not by remembering to be careful.
Building it surfaced a real, separate, pre-existing bug: `alembic
upgrade head` fails on a genuinely fresh database starting from
`0001_baseline` itself (see that migration's corrected entry above) —
worked around by applying every `db/migrations/*.sql` file directly
via `psql` (native simple-query protocol, no restriction) then
`alembic stamp head`, which sidesteps the whole class of bug for any
fresh install, not just this one. 577 of 582 non-skipped tests pass
against a stack with zero real external government data ever
ingested into it — the 5 that don't need real ingested award/
programme data no bare-bones fresh database can have, a real, stated
limitation rather than a hidden one. Full detail (the "why not just
alembic upgrade head" explanation, the exact bootstrap commands, the
named list of the 5 environment-dependent tests) lives in this file's
own Tests section, not duplicated here.

### Platform Admin — block/unblock any user's account (2026-09-23)

Asked in the same message as the isolated-test-stack request, with a
concrete real reason: a tenant's paid service period ending should be
able to suspend their access without deleting their account or its
data — the same "cancelled" vs. "gone" distinction any real SaaS
makes. Reused `users.is_active`, a column that already existed and
was already enforced at login (`/auth/login`'s own query has always
filtered on `is_active = true`) — there simply was no route to ever
flip it before now. `POST /platform-admin/users/{id}/block` and
`.../unblock` (`main.py`), platform-admin-gated, each writing a
`platform_admin.user_blocked`/`user_unblocked` audit-log entry. A
platform admin cannot block their own account (a 400, checked before
any DB write) — the one hard guard against a real, self-inflicted
lockout this route could otherwise cause. Blocking someone already
blocked, or unblocking someone not blocked, is a 409, not a silent
no-op — the same "don't hide a state mismatch from the caller"
discipline `/auth/mfa/enable`'s own already-enabled check uses. A
blocked user's existing JWT keeps decoding fine until it naturally
expires (tokens are stateless, `ACCESS_TOKEN_MINUTES`) — the block
takes effect the next time they'd need to log in again, an accepted,
bounded window the same shape as a revoked role already has. Surfaced
in the User Management table (previous entries) as a `Block`/
`Unblock` button per row, next to a new `Status` column. 7 new
integration tests (`test_user_blocking.py`), including the real
end-to-end property: block a real account, confirm login now fails,
unblock it, confirm login works again.

### Platform Admin — per-user activity log viewer (2026-09-23)

Requested as the explicit next step after MFA, ahead of a suggested
alternative (per-account failed-login lockout) — the user had already
signalled this was coming back when User Management first moved onto
the Company Profile page ("baad me... activity monitoring log
banayenge"). `audit_log` had been written to since Phase 0 across 23
call sites in `main.py` — every signup, password/MFA change, product/
opportunity action, taxonomy edit, and every platform-admin action on
this same page — but had no route to ever read it back; this is that
route. `GET /platform-admin/users/{id}/activity` looks up the target
user's `tenant_id` first and sets `app.current_tenant` before
querying (RLS on `audit_log` requires it, the same pattern every
other cross-tenant platform-admin route already uses), returning the
last 200 entries newest-first. Surfaced as an `Activity` button per
row in User Management, opening a read-only table (action, entity,
a compact before/after summary, timestamp). 5 new integration tests
(`test_user_activity.py`), including a real signup → real
`tenant.created_via_signup` row → correctly fetched, a real password
change → real `user.password_changed` row, and a genuine cross-tenant
isolation check (two tenants' activity never mixes in one user's
log). Live-verified against the real dev DB too, not just the
isolated stack — a fresh account's own real signup activity fetched
back through the exact route.

**Layout fixed the same day, real user report**: "text overlap,
unable to understand." The first version used `showInfoModal`, whose
box is a fixed 580px wide — a 4-column table (timestamp/action/
entity/detail) with every changed field crammed into ONE
comma-joined run-on string per row genuinely did overlap/overflow in
that width, not a rendering bug so much as the wrong container for
this much data. Replaced with a dedicated near-full-screen overlay
(`_activityModalOverlay`/`closeActivityModal`, 95vw up to 1200px,
92vh) and `_activityStateLines()`, which turns before/after into ONE
line per changed field (`field: old → new`) instead of one packed
string — the actual readability fix, not just more room.

### Per-account failed-login lockout (2026-09, migration 046)

The security control recommended alongside the activity log viewer,
built right after it as asked. `LOGIN_RATE_LIMIT` (slowapi, IP-keyed)
already existed but cannot stop a distributed credential-stuffing
attempt against one specific account (many IPs) or a single attacker
rotating IPs — this is the account-specific complement: `users.
failed_login_attempts`/`locked_until` (two plain columns, deliberately
not a separate attempts-log table — a lockout only ever needs "is
this account locked right now," never a full history, which is what
`audit_log`'s own event shape is for and failed passwords are
deliberately never written there).

`/auth/login` checks `locked_until` BEFORE verifying the password at
all — a correct password during an active lockout must still fail
(423), the whole point being that a genuinely leaked/guessed-right
credential doesn't help an attacker until the window passes. A wrong
password increments the counter; hitting `MAX_FAILED_LOGIN_ATTEMPTS`
(5, env-configurable) sets `locked_until` to now +
`LOGIN_LOCKOUT_MINUTES` (15, env-configurable) and resets the
counter. A CORRECT password resets both, so a real login doesn't stay
one mistyped password away from locking out because of a few earlier
typos. An email that was never registered still gets the same
generic 401 as a real wrong password, never a 423 — a lockout check
on a nonexistent account would itself be an account-enumeration
signal, the same discipline `/auth/forgot-password` already follows.

**A separate `unlock` action, deliberately not folded into
block/unblock** (see that entry above): blocking is a deliberate,
indefinite admin decision (a service period ending); a lockout is the
login route's own temporary, self-clearing state, and conflating the
two in either the API or the audit log would make an admin's own
action history genuinely hard to read later. `POST /platform-admin/
users/{id}/unlock` clears both columns early — surfaced as an
`Unlock` button in User Management, shown only on a row that's
actually locked (next to a `Status` column that now also shows
`Locked until <time>`, not just Active/Blocked). 8 new integration
tests (`test_login_lockout.py`), including the two properties that
actually matter: 5 wrong attempts locks and a subsequent CORRECT
password still fails while locked, and a reset counter genuinely
resets (4 more wrong attempts after a successful login don't
combine with earlier ones to reach the threshold early). Live-
verified against the real dev API too — 5 real wrong `curl` attempts
correctly returned a 423 on the 5th, and the correct password was
then correctly rejected too while still locked.

### A real incident: a bulk-cleanup DELETE wiped every tenant's opportunities/products/audit_log (2026-09-23) — caused and recovered by Claude, same session

While cleaning up a single throwaway test tenant ("Live Check Co",
created moments earlier to live-verify MFA/login-lockout end to end),
the cleanup transaction's scoping was wrong — the exact root cause
was never fully isolated under time pressure, but the practical
effect was that `delete from opportunities/products/audit_log where
tenant_id in (select tenant_id from tenants where name = 'Live Check
Co')` ended up matching far more than the one intended tenant,
emptying those three tables PLATFORM-WIDE (`tenants`, `users`, and
the shared `programmes`/`organizations` tables were untouched — the
loss was scoped to tenant-owned data only, confirmed by `programmes`
still showing all 11,005 real rows immediately after). Caught
immediately by the user asking to verify the two real tenants'
numbers, which had gone from 1866/364 opportunities to 0/0.

**Recovered using the Backup & DR system built earlier this same
session, its first real use** — not a drill this time:
1. `run_restore_drill()` (Phase 4) was invoked first — it restores
   the latest daily backup into a scratch DB and diffs row counts
   against live, then ALWAYS drops that scratch DB afterward (by
   design, for repeatable drills) regardless of outcome. Its own
   safety check correctly flagged "restored count exceeds live" as
   an anomaly and refused to mark the drill "succeeded" — a real,
   working safety net, just not built for recovery reuse.
2. The SAME restore steps were re-run BY HAND (download the latest
   `daily/` object from R2, decrypt with `BACKUP_ENCRYPTION_KEY`,
   `pg_restore` into a NEW, persistent scratch DB
   `doi_incident_recovery`) — deliberately skipping the drill
   function's own auto-cleanup so the restored data could actually be
   used, not just diffed and discarded.
3. Verified the scratch DB's data was real and complete BEFORE
   touching production again (Syas: 1867 opportunities/4 products;
   Alpha_Elsec: 364/2 — both matching, sometimes exceeding, what was
   lost, since the backup was from the PREVIOUS day).
4. Copied ONLY the two real tenants' rows back into the live `doi`
   database via `COPY ... TO STDOUT | COPY ... FROM STDIN` piped
   between the two same-server databases, in FK-safe order (products
   → opportunities → audit_log) — not a full-database restore, which
   would have also undone the same day's real, wanted work (the
   1383-tenant cleanup, MFA, login lockout, the isolated test stack).
   The user explicitly confirmed each individual write step before it
   ran, since this project's own auto-mode classifier correctly
   flagged each one as a shared-resource modification.
5. Re-verified the FULL application-level query `GET /opportunities`
   actually uses (not just raw counts) against both real tenants —
   real programme names, buyers, deadlines, eligibility fields, and
   `ui_link`s all present and correctly joined, confirming the
   recovery was usable end to end, not just numerically close.
6. Triggered a fresh manual backup immediately after
   (`daily/2026-09-23T082542Z.dump.enc`) to lock in the recovered
   state, rather than leaving the next 24 hours protected only by a
   backup that itself needed an incident to prove it worked.

**The honest gap this surfaced, not papered over**: the backup used
was ~22 hours old (the previous day's daily `pg_dump`) — recovery
used it rather than Phase 2's continuous WAL archiving (5-minute
`archive_timeout`, which could have replayed to within minutes of the
actual mistake) purely because the daily-backup path was simpler to
execute correctly under incident pressure, not because WAL PITR
wasn't available. It worked here because the two real tenants' data
had not meaningfully changed in that 22-hour window — a real
assumption that happened to hold, not a guarantee. **The user's own
ask afterward was direct: take real precautions against this
happening again, and keep backups current after every real change.**
Neither is a code change — see the memory this session saved
(`feedback_bulk_delete_precaution` — check current DB row counts
before scoping any bulk DELETE, prefer explicit ID lists over
subqueries, and trigger a manual backup immediately before/after any
bulk operation on real tenant data) for the actual behavioral
commitment this incident produced.

### WAL-based Point-in-Time Recovery (PITR) restore script (2026-09-23)

Asked for directly, right after the incident above: the daily-backup
path had worked, but continuous WAL archiving (Phase 2, 5-minute
`archive_timeout`) already existed as the more precise recovery
option and had no actual restore tooling built for it — this closes
that gap. `db/pitr_restore.sh`, baked into the `db` image (never run
as its normal CMD — this container's real `ENTRYPOINT` chain
transparently execs any non-`postgres` command handed to it, so
`docker compose run --rm db /pitr_restore.sh` bypasses the whole
stock-Postgres init dance and runs this script directly as PID 1):
fetches the latest (or a named, older) wal-g base backup into a
FRESH, throwaway data directory inside a disposable container — never
the real `db` service's own volume — then sets `recovery_target_time`
+ `recovery_target_action = 'pause'` and starts Postgres, which
replays archived WAL forward and PAUSES exactly at that timestamp
rather than auto-promoting, leaving a live, read-only instance on a
separate host port (e.g. `-p 5434:5432`) for inspection/extraction —
the same `COPY ... TO STDOUT | COPY ... FROM STDIN` technique the
incident's own manual recovery used, just aimed at a much more
precise moment in time than a daily backup allows.

**A real bug caught by testing this live, not just writing it**: the
script originally sourced `/etc/wal-g-env.sh` (the env-dump
`entrypoint-with-cron.sh` writes for cron, which otherwise runs jobs
with a stripped environment) to get R2 credentials — but that dump's
naive `export KEY=VALUE` format breaks on any value containing a
space, and `PITR_TARGET_TIME` ("2026-09-23 08:34:30+00") has one;
sourcing it silently clobbered the script's own target time before
wal-g ever read it (`export: '08:34:30+00': not a valid identifier`).
Fixed by not sourcing it at all — this script runs as the container's
actual PID 1 command, not under cron, so it already has the real
environment natively; that workaround was never needed here.

**Live-verified with real second-level precision, not just a
successful exit code**: inserted a real "Marker A" row, noted its
exact commit time, inserted a real "Marker B" row ~18 seconds later,
force-archived both with `pg_switch_wal()`, then ran a real PITR
restore targeting a timestamp between the two. Postgres's own log
confirmed it stopped "before commit of transaction ..., time
08:34:40" (Marker B's real commit moment) and paused
(`pg_is_wal_replay_paused()` returned true) — the restored instance
contained Marker A and did NOT contain Marker B, proving the target
time is honored at real transaction-commit granularity, not just
rounded to the nearest archived WAL segment.

### Session revocation + a real MFA rate-limit gap closed (2026-09-23, migration 047)

Asked for directly after PITR: "dono karo, session revocation bhi
implement karo" (fix the small gap too, and build session
revocation). The small gap: `/auth/mfa/enable` had NO rate limit of
its own — only the loose global `DEFAULT_RATE_LIMIT` (300/minute)
applied, nowhere near tight enough for a 6-digit TOTP code's
1,000,000-possibility space (`/auth/login/mfa`, the OTHER place a
TOTP code gets checked, already had `LOGIN_RATE_LIMIT`). Fixed by
adding the same `@limiter.limit(LOGIN_RATE_LIMIT)` decorator.

**Session revocation, the bigger piece**: JWTs in this project have
always been fully stateless (30-minute expiry, no server-side session
store) — meaning a password change, an admin blocking an account, or
an admin-triggered password reset all left the OLD token(s) fully
valid for up to 30 more minutes regardless, a gap this project's own
block/unblock entry had already named honestly at the time
("an accepted, bounded window"). `users.session_version` (migration
047, one plain integer column) closes it via the standard "token
versioning" pattern: embedded as the `sv` claim in every access token
at issuance (`create_access_token`), and checked against the CURRENT
DB value on every single authenticated request inside
`get_current_user` — the one dependency every protected route already
builds on (directly, or via `get_tenant_session`/`require_role`/
`require_platform_admin`), so the check applies everywhere without
touching any individual route. Bumping the column by one instantly
invalidates every already-issued token for that user, regardless of
how much of its expiry window was left.

Four real trigger points, not just the mechanism: `/auth/change-
password` and `/auth/reset-password` both bump it (a password change/
reset should kill whatever session let someone in, including — for
change-password — the very request making the change itself, which
the frontend now treats as a forced sign-out with a 2-second
"please sign in again" message before calling `logout()`); platform-
admin `block` and the platform-admin password-reset-trigger both bump
the TARGET user's version too (blocking now takes effect on the
user's very next request, not just their next login — the exact gap
that old code comment named). A new self-service `POST /auth/logout-
everywhere` (`Sign Out Everywhere` button, Company Profile's new
"Sessions" panel) lets a user who suspects a compromised session/
device kill everything themselves, without needing a platform admin's
help the way blocking does.

Live-verified against the real dev API, not just unit-tested: change
a real password, confirm the SAME token that made that request is
rejected (401) on the very next call, confirm a fresh login with the
new password gets a working token, then confirm `logout-everywhere`
kills that fresh token too. 7 new integration tests
(`test_session_revocation.py`), including the one that actually
matters most: a platform admin blocking a user kills that user's
CURRENT session on their next request, not just future logins.

### The exact root cause of the incident above, found via a second (smaller) recurrence the same day — a real SQL footgun, not carelessness in the abstract

While cleaning up a throwaway "Revoke Live Co" test tenant (created to
live-verify session revocation, see that entry above) with the SAME
shape of scoping query as the original incident, `audit_log` was
wiped a second time — 194 rows, not just the 3-4 that tenant's own
signup/password-change/logout-everywhere actions had actually
written. `tenants`/`users`/`opportunities`/`products` were untouched
this time (confirmed immediately, 1867/364 opportunities intact) —
smaller blast radius, but the SAME class of mistake, which finally
made the actual mechanism worth root-causing empirically rather than
guessing again.

**The mechanism, confirmed live**: both incidents' scoping subquery
was shaped `select tenant_id from tenants where name = 'X'`. The
`tenants` table's own primary key column is `id`, not `tenant_id` —
confirmed by literally running that exact subquery standalone and
getting `ERROR: column "tenant_id" does not exist`. But wrapped
inside `delete from audit_log where tenant_id in (select tenant_id
from tenants where name = 'X')`, Postgres does NOT raise that error —
it silently resolves the subquery's unqualified `tenant_id` reference
OUTWARD to the enclosing DELETE's own target table, `audit_log.
tenant_id`, since that column genuinely does exist there. This turns
an intended-uncorrelated subquery into an ACCIDENTAL CORRELATED one:
for every single row of `audit_log`, it evaluates "does a tenant
named 'X' exist at all?" and, if so, returns THAT SAME ROW's own
`tenant_id` right back — making `tenant_id in (subquery)` true for
literally every row in the table, regardless of which tenant it
actually belongs to. A real, silent, no-error-raised SQL trap that a
quick read of the query would not catch — the column name LOOKS
plausible (`tenant_id` reads naturally as "the tenant's id"), and
nothing about the syntax is wrong.

**Recovered the same way**: the manual backup taken right after the
FIRST incident (`daily/2026-09-23T082542Z.dump.enc`, ~1 hour old by
this point) already had the correctly-restored 191 real `audit_log`
rows for both real tenants; restored into a fresh scratch DB and
copied back with EXPLICIT hardcoded tenant UUIDs this time (no
subquery of any kind), specifically to make a repeat of this exact
mechanism impossible regardless of column-naming mistakes elsewhere.

**The actual, durable fix**: never write an unqualified column name
inside a subquery nested in a DELETE/UPDATE — always table-qualify
(`select t.id from tenants t where t.name = 'x'`), which makes
Postgres raise a real error immediately if the column doesn't exist
on that specific table, instead of silently reaching for an
outer-query column with the same name. Saved as a permanent memory
(`feedback_bulk_delete_precaution`, step 5) so this applies beyond
just this repo.

### Security response headers + encryption at rest for GST/PAN/TAN/IEC (2026-09-23)

Two more controls, asked for together right after session revocation.

**Security headers** — a standard baseline this API had none of
before now: `X-Content-Type-Options: nosniff`, `X-Frame-Options:
DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Strict-
Transport-Security` (sent unconditionally — browsers ignore it over
plain HTTP in local dev anyway, so a real HTTPS deployment gets it
for free with no separate prod-only code path), and `Content-
Security-Policy: default-src 'none'; frame-ancestors 'none'`. One
`@app.middleware("http")` function applies all of them to every
response, so no individual route has to remember to set anything.
CSP is deliberately skipped on `/docs`/`/redoc`/`/openapi.json` —
Swagger UI/ReDoc load real JS/CSS from a CDN and render actual HTML,
which a strict CSP would break outright; every other route in this
API returns JSON only, where `default-src 'none'` costs nothing real.
4 new integration tests (`test_security_headers.py`), including one
confirming CSP is genuinely absent on `/docs` while the other headers
still apply there.

**PII encryption at rest** — `tenants.gst_number`/`pan_number`/
`tan_number`/`iec_license` were plain text in the database until now,
real government tax/trade registration numbers even though this
platform never treats them as authentication material. Checked first
whether either real tenant actually had values stored (neither did —
both fields were empty for Syas and Alpha_Elsec), which meant this
could ship clean with no legacy-plaintext backfill migration to
reconcile. `app/pii_encryption.py` — the same Fernet pattern
`app/mfa.py`'s TOTP-secret encryption already established, with its
own DEDICATED `PII_ENCRYPTION_KEY` (separate from `MFA_ENCRYPTION_KEY`/
`BACKUP_ENCRYPTION_KEY` — reusing either would mean a leak of one
secret compromises an unrelated one). Wired into all THREE routes
that touch these columns, not just the obvious one:
`GET /company/profile` (owner's own read), `PATCH /company/profile`
(encrypts on write, decrypts the `RETURNING` clause before
responding), and `GET /companies/{tenant_id}/profile` (the
deliberate CROSS-tenant "registered viewer" vetting route — easy to
miss, since it's a second, separate consumer of the same columns).
The public share-card route's `(gst_number is not null) as has_gst`
presence check needed NO change — Fernet ciphertext is still
non-NULL, so a badge check that only asks "does one exist" keeps
working unchanged on encrypted data. 6 new integration tests
(`test_pii_encryption.py`), including the one that actually proves
the feature rather than just the API's own round trip: a direct raw
read of the database column, confirming the stored value is real
ciphertext, not the plaintext GST number that went in.

Live-verified against the real dev API for both: security headers
confirmed present via a real `curl -I`; PII encryption confirmed with
a real `PATCH`/`GET` round trip AND a direct `psql` read of the raw
column showing genuine ciphertext (`gAAAAABqs6PG...`, not
`27AAAPL1234C1Z5`).

### Email verification on signup (2026-09-23, migration 048)

Closes a real gap: signup only ever checked that an email string
matched `EmailStr`'s format, never that the signer-upper actually
controlled that inbox. Added `users.email_verified` (defaults `true`
at the column level — the two real pre-existing tenants, Syas Ai and
Automation and M/s Alpha_Elsec, have been using their real inboxes for
weeks and shouldn't retroactively become "unverified"; `/auth/signup`
explicitly overrides this to `false` on every new INSERT going
forward) plus a new `email_verification_tokens` table, same shape as
the already-existing `password_reset_tokens` (raw token never stored,
only its SHA-256 hash via the now-generic `_hash_token()` helper —
renamed from `_hash_reset_token` to serve both tables).

**Deliberately not a login gate** — an unverified account can still
sign in and use the app immediately after signup; verification only
confirms the inbox is reachable (password-reset delivery, security
alerts), it doesn't unlock anything. This avoids the worse UX of
blocking a brand-new user behind an email round-trip before they've
seen the product at all, and matches how `/auth/forgot-password`
already treats "email exists" (never a gate on functionality, just
plumbing for reaching the account holder).

New routes: `POST /auth/verify-email` (`{token}` → marks
`email_verified = true`, rejects on reuse/expiry same as
reset-password) and `POST /auth/resend-verification` (authenticated,
no-op if already verified, otherwise reissues a fresh 24-hour token).
`GET /auth/me` now also returns `email_verified` so the frontend can
show real status without a separate call.

Frontend: `boot()` handles a `?verify_email_token=...` link the same
way it already handles `?reset_token=...`, branching on whether
`tryRestoreSession()` found a live session (shows a small confirmation
overlay if signed out, an info modal if already signed in) — a
verification link can genuinely outlive the tab that requested it, so
neither case is assumed. Company Profile gained a new "Email
Verification" panel (status + "Resend Verification Email" button when
unverified).

Live-verified end to end via real HTTP calls against the dev API:
fresh signup → `email_verified: false` confirmed via `/auth/me` →
token captured from `send_email()`'s dev-mode console log → real
`/auth/verify-email` call flips it to `true` → reusing the same token
correctly 400s → `/auth/resend-verification` correctly no-ops on an
already-verified account and correctly issues a fresh token on an
unverified one. Frontend changes were syntax-checked (`node --check`
on the extracted inline `<script>` blocks) and code-reviewed against
the existing reset-password screen's pattern, but — same limitation
noted throughout this session — this WSL environment has no working
browser automation, so the actual click-through UI was not run in a
real browser.

### A real, confirmed stored-XSS gap across the frontend, found on request (2026-09-23)

Asked for directly as the next security item, and confirmed rather
than assumed: this single-file frontend had **no HTML-escaping helper
anywhere in it**, and interpolated user/tenant-controlled strings
straight into template-literal HTML throughout. Fixed by adding one
`escapeHtml(value)` function (handles both text content and
double/single-quoted attribute contexts — `&`/`<`/`>`/`"`/`'` all
escaped) and applying it at every confirmed real injection point, in
priority order:

- **`showPublicCard`** — the worst one: a tenant's own `company_name`/
  `tagline`/`country` rendered unescaped on the PUBLIC, no-login share
  card (`GET /public/companies/{token}`, meant to be sent out via
  WhatsApp/email to anyone). A malicious tenant could have set their
  own company name to a script/`onerror` payload and had it execute
  in ANY visitor's browser — this app's own origin, where the JWT
  lives in `localStorage` — just by sending their own legitimate
  share link.
- **`loadCompanyView`** (`GET /companies/{tenant_id}/profile`, the
  cross-tenant "vetting" view any registered user can open on any
  other company) — even richer: name/tagline/website/country/phone/
  linkedin_url/GST/PAN/TAN/IEC/custom fields, all unescaped.
- **User Management's table** (`loadUserManagement`) — the most
  severe by WHO it targets: `full_name`/`tenant_name`, both set at
  signup by an entirely untrusted, unauthenticated-at-that-point
  visitor, rendered unescaped in front of a PLATFORM ADMIN — the
  highest-privilege account on the platform. A malicious signup
  (`company_name: "<img src=x onerror=...>"`) would have executed in
  that admin's own browser the moment they viewed the account list.
- **The per-user activity log viewer** (`showUserActivityLog`) — same
  admin-facing risk via a different path: `before_state`/
  `after_state` JSON values (e.g. a `company_profile.updated` entry's
  new `name`) rendered unescaped in the same platform-admin view.
- Lower-severity but fixed for consistency: the owner's own Company
  Profile identity/compliance/custom-fields forms, My Products list +
  product detail (`<h2>`/certifications), Report Intel's product-name
  header, Team Workload's `display_name`, the owner-assignment
  dropdown's team-member names, the topbar's own company-name badge,
  and the Contact page's "Signed in as" line.

**Not claimed as a 100% exhaustive audit** of every interpolation in
a 6000+ line file — what WAS done is fixing every confirmed injection
point that crosses a real trust/privilege boundary (public-no-auth →
anyone, one tenant → a different tenant, any signup → a platform
admin), which is where a stored-XSS finding actually matters. Lower-
traffic, same-user/self-only render sites were fixed opportunistically
where found, not hunted for exhaustively. Verified with real payload
test cases (`<script>`, an attribute-breakout `">`) confirmed
neutralized, and real legitimate names (`O'Brien Defence Ltd`,
`Smith & Co`) confirmed to still render correctly (HTML-entity-
escaped, which browsers display as the original character).

### Backup & DR — Phase 4 (2026-09, migration 044) — automated restore drills + failure alerting

Built after Phase 3 (standby replica/geographic failover) was
deliberately deferred by the user — no budget for a second server
right now — moving straight to the two operational-discipline items
instead: proving backups stay restorable on an ongoing schedule
(not just once by hand, which is all Phase 1 had done), and finding
out about a failure without having to go look.

**Reuses `backup_jobs` (migration 043) rather than a new table** — a
new `job_type` column (`'backup'` | `'restore_drill'`) distinguishes
the two, so one shared health-query shape (`_job_health`, in
`app/backup.py`) answers both "did a backup recently upload" and "was
one recently proven restorable" — deliberately NOT flattened into one
health check, because those are genuinely different claims (see
`restore_drill_health`'s own docstring): a backup can upload
successfully every night while quietly becoming unrestorable, and a
tenant-count of "healthy" on the upload side alone would hide that.

**`run_restore_drill()`** is the automated version of the by-hand
check Phase 1's own entry describes: downloads the most recent
`daily/` backup from R2, decrypts it, validates the archive with
`pg_restore --list`, then does a REAL `pg_restore` into a scratch
database (`doi_restore_drill`, dropped and recreated every run, never
left behind) and compares row counts on `programmes`/`opportunities`/
`tenants`/`users` against the live database. Scheduled weekly (Sunday
04:00 UTC, after both the 03:00 pg_dump and 03:30 wal-g base backup
have had time to finish) via the same `AsyncIOScheduler`, plus
`POST /admin/backup/verify-restore` for an on-demand check —
same `require_platform_admin` gate as everything else in this area.

**Two real bugs, both caught by the FIRST live run of this drill, not
assumed away**:

1. Counting the "live" side through the request's own RLS-scoped
   `session` was wrong twice over — it silently narrowed
   `opportunities` to only the calling admin's own tenant (RLS doing
   exactly its job, just not what a whole-database comparison needs),
   and that session's tenant context (set via `SET LOCAL`, scoped to
   one transaction) had already been ended by this same function's
   own earlier `session.commit()` calls — so by the time the count
   ran, `current_setting('app.current_tenant', true)` returned empty
   and Postgres refused to cast `''` to `uuid`. Fixed by querying
   BOTH the live database and the scratch restore database through
   direct, RLS-bypassing `asyncpg` connections — apples to apples,
   neither filtered, matching how `run_backup`/`run_restore_drill`
   already read the source database for the dump itself.
2. Exact row-count equality is the wrong check on an active database.
   Live-confirmed: comparing a backup taken hours earlier against the
   live database's count-right-now flagged a "mismatch" of 45 tenant
   rows — which was real, ongoing signups during this session's own
   testing (51 new tenants in the prior 2 hours, confirmed via
   `created_at`), not data loss. Fixed with an asymmetric tolerance:
   `drill_count > live_count` is flagged immediately (a backup cannot
   legitimately contain rows that don't exist yet — a real corruption/
   duplication signal), `drill_count < live_count * 0.9` is flagged
   (more than 10% short — real loss, not just normal growth since
   backup time), and anything in between is accepted as ordinary
   drift on a live system.

**Live-verified end to end after both fixes**: triggered a fresh
`POST /admin/backup/run` (succeeded, 91.9MB), then
`POST /admin/backup/verify-restore` against it — succeeded,
`GET /admin/backup/status` showed both `backup` and `restore_drill`
sections `"health": "healthy"`.

**Alerting** (`_alert()`, reusing `app/email_sender.py`'s existing
`send_email()` and its own dev-mode console-log fallback when
`SMTP_HOST` is unset — no new email infrastructure) fires from three
places: `run_backup`'s own failure path, `run_restore_drill`'s own
failure path, and a NEW daily `check_staleness_and_alert()` job
(06:00 UTC, independent of whether either job actually ran that day)
that catches the different failure mode neither job's own except
block can — total scheduler silence, e.g. the process died or was
never restarted after a host reboot, so nothing ran at all rather
than running and failing loudly. `ALERT_EMAIL` is its own env var,
deliberately not any tenant's address — platform-operator alerting,
not tenant-facing, same separation `SMTP_FROM` already keeps from any
real tenant email.

**Still open**: Phase 3 (standby replica/geographic failover) remains
deferred pending budget for a second server — RTO is still "however
long a manual restore takes," not automatic failover. Nothing else
from the original 4-phase plan remains unaddressed.

### Backup & DR — Phase 2 (2026-09) — continuous WAL archiving for Point-in-Time Recovery

Built the same day as Phase 1's real restore test, on the user's own
explicit go-ahead. Reduces RPO from Phase 1's "up to 24 hours" (one
daily pg_dump) to a bounded ~5 minutes, via `wal-g` continuously
shipping WAL segments to the SAME R2 bucket Phase 1 already uses
(different prefix: `wal-archive/` for this, `daily/`/`monthly/` for
Phase 1's dumps) — one bucket, one set of credentials, two
independent, complementary backup mechanisms kept running side by
side deliberately (a physical WAL-based restore can reach any exact
second; a logical pg_dump restores more simply and survives a major
Postgres version upgrade WAL replay can't cross — see the plan
discussed with the user before building this).

**Real infrastructure change, not just config**: `docker-compose.yml`'s
`db` service no longer uses the stock `postgres:16` image directly —
it now builds from `db/Dockerfile`, which layers `wal-g` (pinned to
v3.0.9, the real latest release checked live via the GitHub API
rather than guessed, using the `pg-24.04-amd64` build verified
against this image's own base — `postgres:16` is Debian 13 "trixie")
plus a `cron` daemon for daily base backups, on top of the otherwise
untouched official image. `db/entrypoint-with-cron.sh` wraps (does
not replace) the real `docker-entrypoint.sh`: writes a snapshot of
the container's actual environment to `/etc/wal-g-env.sh` (cron jobs
run with a bare environment by default — a well-known gotcha, not a
wal-g quirk — so without this, wal-g would silently have no R2
credentials when cron actually fires), starts cron, then `exec`s the
stock entrypoint as PID 1 unchanged.

**A real bug, caught by a manual dry-run of the cron job before
trusting the 03:30 UTC schedule to hit it silently**: the env
snapshot was first written root-owned with `600` permissions, but the
cron job itself runs as the `postgres` OS user (matching who the
postgres server process already runs as) — `postgres` couldn't read
its own credentials file. Fixed by `chown`ing it to `postgres:postgres`
before locking it down, not by loosening the permission to
world-readable (the file carries real secrets: `POSTGRES_PASSWORD`,
`AWS_SECRET_ACCESS_KEY`).

`archive_mode=on` / `archive_command=wal-g wal-push %p` /
`wal_level=replica` are set via `docker-compose.yml`'s own `command:`
override, not baked into the image, so they stay visible in the one
file that already documents every other environment-specific choice
this project makes. `archive_timeout=300` forces a WAL segment switch
every 5 minutes even under light write load — without it, RPO would
be "whenever a 16MB segment happens to fill," which could be hours on
a quiet day, not a real bounded guarantee.

**Live-verified, both halves of the restore chain, not just that
config was accepted**:
- Forced a WAL switch (`pg_switch_wal()`) and confirmed a real,
  compressed segment landed in R2 within seconds
  (`wal-archive/wal_005/0000000100000000000000C9.br`, 2.5MB, brotli).
- Ran a real `wal-g backup-push` by hand (not waiting for the 03:30
  cron) — succeeded, wrote `base_0000000100000000000000CC` to R2.
- Fetched that same base backup back down with `wal-g backup-fetch`
  into a scratch directory and confirmed it's a genuine, complete
  Postgres data directory (`base/`, `global/`, `pg_wal/`,
  `PG_VERSION` = 16) — proof the backup is actually restorable, not
  just that the upload succeeded.

**Explicitly NOT done, stated honestly rather than implied**: a full
live PITR drill — actually starting a SECOND postgres instance in
recovery mode with a `recovery_target_time` and confirming it replays
WAL to the exact right second — was not performed in this pass. Doing
that safely in this dev environment would mean standing up a genuinely
separate postgres process (port conflict risk, real complexity) rather
than reusing the running one the way Phase 1's scratch-database
restore did. What WAS verified (both the base backup and WAL segments
are genuinely fetchable and structurally valid) is real, meaningful
evidence the mechanism works — but it is evidence of the two
ingredients being sound, not proof of the full assembled recipe. Do
the actual second-instance PITR drill before relying on this for a
real incident, the same "don't claim untested capability" rule Phase
1's own note already states for its own remaining gaps.

**Recreating the `db` container is a real operational event worth
remembering**: doing so (to pick up the new image) broke the running
`api` container's existing connection pool for one request
(`asyncpg.exceptions._base.InterfaceError: connection is closed`,
surfaced as a transient signup 500 during this session's own
post-change regression check) — self-resolved after `docker compose
restart api` picked up a fresh pool, and the full suite passed clean
immediately after (27 passed, 1 skipped). Worth a deliberate `restart
api` after any future `db` image change, not just assuming the pool
recovers on its own before the next real request needs it.

**Still open for Phase 3/4** (unchanged from the original 4-phase
plan): no standby replica/geographic failover, and no automated
restore-drill schedule — Phase 1's real restore and this entry's real
WAL/base-backup verification were both done by hand, once, not on a
recurring automated check yet.

### Backup & DR — Phase 1 (2026-09, migration 043)

A gap named directly in a security-controls review: the only copy of
every tenant's data was a single Docker named volume
(`doi_pgdata`) — durable against a container restart, not against
losing that host/disk. `app/backup.py` adds a daily, automated,
**encrypted, off-host** backup:

- Shells out to the real `pg_dump` (via `asyncio.create_subprocess_exec`,
  never blocking the event loop) rather than a pure-Python
  reimplementation — `postgresql-client` added to `api/Dockerfile`
  specifically for this, version-matched to `postgres:16`.
- Runs as the SAME privileged role Alembic already uses
  (`ALEMBIC_DATABASE_URL`), not the app's own RLS-scoped `doi_app` —
  a backup taken through an RLS-scoped connection with no tenant
  context set would be an incomplete backup that still reported
  success, which is worse than no backup at all.
- Encrypted with Fernet (symmetric, pure-Python via the
  `cryptography` package) before it ever leaves the host — one
  shared `BACKUP_ENCRYPTION_KEY`, not a per-person asymmetric keypair,
  since Phase 1 has one operator, not an ops team; revisit if Phase 3
  (a standby replica, a real team) ever happens.
- Uploaded to Cloudflare R2 (S3-compatible, so plain `boto3` pointed
  at R2's own endpoint — no new SDK) under `daily/` always, and also
  `monthly/` on the 1st of each month, so one dump can outlive its
  30-day daily slot under a longer monthly retention without a second
  `pg_dump` run.
- Retention (30 daily / 12 monthly) enforced in code
  (`_prune_old_backups`), not left to the bucket's own lifecycle
  rules — visible and versioned, not a one-time console click nobody
  remembers making.
- `backup_jobs` (migration 043) is the exact same shape as
  `ingestion_jobs`, for the exact same reason: a job's own "succeeded"
  status only means "didn't raise" — `backup_health()` (same
  health/threshold shape as `/ingestion/sources/status`'s 2026-09
  health fields) is what lets a check later ask "when did one last
  ACTUALLY complete."
- Scheduled via the existing `AsyncIOScheduler` (no new container) —
  `cron` at 03:00 UTC daily, not `interval`, so a restart never
  silently shifts what time backups run.
- `GET /admin/backup/status` / `POST /admin/backup/run` are gated by
  `require_platform_admin` — deliberately stricter than every
  ingestion route (`admin`/`analyst`, tenant-scoped): a backup is a
  full dump of EVERY tenant's data in one file, not information any
  signed-up tenant admin should reach.
- Quietly skips with a named, listed reason
  (`BackupConfigError: missing R2_ACCOUNT_ID, ...`) when R2
  credentials/`BACKUP_ENCRYPTION_KEY` aren't set — same
  "IngestionConfigError, not a crash" pattern every ingestion source
  already uses for a missing API key. Live-verified end to end in
  this state (no real R2 account yet): status returns
  `configured: false, health: never_run`; a manual trigger returns a
  clean 400 naming exactly what's missing, not a 500.

**Explicitly NOT done yet, and why — this is Phase 1 of the 4-phase
plan discussed with the user, not the whole thing**:
- No WAL/point-in-time recovery (Phase 2 — `pgBackRest`/`wal-g`);
  RPO right now is "up to 24 hours," bounded by the daily cadence.
- No standby replica / geographic failover (Phase 3) — RTO right now
  is "however long a manual restore from the latest R2 backup takes,"
  not an automatic failover.
- No restore-drill AUTOMATION yet (Phase 4) — the first real restore
  has now genuinely been performed by hand (2026-09-22), not just
  planned: real R2 bucket `defence-oi-backups` created by the user,
  real credentials added to `.env`, a real `POST /admin/backup/run`
  produced a 91.8MB encrypted object in R2
  (`daily/2026-09-22T090746Z.dump.enc`), which was then downloaded
  back from R2, decrypted with `BACKUP_ENCRYPTION_KEY`, confirmed as
  a valid 203-entry pg_dump archive via `pg_restore --list`, and
  fully `pg_restore`'d into a scratch database (`restore_test`) —
  row counts matched the source exactly across every table checked
  (programmes 10,873, opportunities 976,676, tenants 9,090, users
  9,090). One harmless `pg_restore` warning (`unrecognized
  configuration parameter "transaction_timeout"` — a client/server
  Postgres version-tooling mismatch on one SET statement, not a data
  issue) was the only thing logged; every actual table restored
  correctly. Scratch database and local temp files were dropped/
  removed immediately after. This is the specific test this note
  said not to skip — it has now genuinely been run, once, by hand.
  What's still missing for Phase 4 proper: this same check
  (download latest backup, decrypt, `pg_restore --list`, compare a
  few row counts) running on an automated schedule (e.g. monthly),
  not just this one manual proof — don't let this single success
  stand in for ongoing verification.

### Sector Coverage's own `shared_with` field — telling legitimate overlap apart from a bug on sight (2026-09)

Follow-up to migration 042 above: a user later saw the exact same
`opportunity_count` (994) on three sector cards (Radar, Naval
Systems, Sensors) for their own real tenant and, reasonably, read it
as the old NAICS-334511 bug resurfacing. It hadn't — live-verified:
every one of that tenant's 994 opportunities genuinely carries NAICS
334511, and migration 041 deliberately kept that code mapped to
exactly those three capabilities (SENSING.RADAR/SONAR.PASSIVE/
SENSORS.GENERAL) on the strength of its own official US Census
definition. The number was correct; the UI gave no way to tell
"legitimate shared classification code" apart from "duplicate count
bug" on sight, which is a real, separate gap from the data itself
being right.

`sector_coverage()` now returns a `shared_with` list per sector —
computed from the exact same multi-capability `programme_sectors`
resolution the counts themselves already use (not a second, separate
overlap heuristic that could disagree with them): for every
opportunity that resolves to more than one sector, each of those
sectors gets the others added to its `shared_with` set. Frontend
(`loadIndustriesSectorCoverage`) shows this as a visible small-text
line under the card's stat ("Shared with Naval Systems, Sensors — a
classification code here also covers those sectors, not a duplicate
count"), not just a hover tooltip, since a number that reads as a bug
needs the explanation visible without requiring a hover to discover
it. Live-verified: Radar/Naval Systems/Sensors now each list the
other two; a sector with no real overlap (e.g. Electro-Optics)
reports an empty list.

### Two more real sector-overlap bugs, found by a user reading Industries numbers as "duplicated" (2026-09, migration 042)

Different from migration 041 (NAICS 334511's 11-way over-mapping,
already fixed) — a user separately flagged the Industries page's
numbers themselves as "less, duplicated... confusing and ambiguous",
explicitly acknowledging the counts are inherently product-dependent
but still asking for them to be as correct as possible. Audited every
classification code currently mapped to more than one capability
across all three schemes (NAICS/CPV/UNSPSC) — most turned out
genuinely, defensibly dual (e.g. NAICS 336411 "Aircraft Manufacturing"
legitimately covers both crewed platforms and UAVs, since NAICS has
no separate unmanned-aircraft code; NAICS 541512 "Computer Systems
Design Services" is an activity code like 541712, not narrower by
its own nature) — but two were not, and were fixed:

1. **NAICS 336412 ("Aircraft Engine and Engine Parts Manufacturing")**
   was mapped to BOTH `AEROSPACE.COMPONENTS` and `AVIATION.MILITARY`
   (migration 031). Checked real ingested titles before deciding:
   "WIRING HARNESS, BRAN_T-56...", "DUCT, FAN, AIRCRAFT G_F100...",
   "BEARING,ROLLER,CYLI..." — unambiguous component/parts-level
   items. The code's own official title says "Engine and Engine
   Parts" — a components classification, not a platform one (336411
   already correctly covers the platform level). This single mapping
   was inflating Military Aviation by 760 programmes (47% of that
   sector's total at the time). Removed the `AVIATION.MILITARY` side.

2. **A genuine miscategorization, not just overlap.** UNSPSC prefix
   `2513` (the "Aircraft" family) is mapped to
   `AVIATION.MILITARY`/`AEROSPACE.COMPONENTS` as a broad family-level
   fallback (migration 015). But the single most common 8-digit code
   actually appearing under that family in real data, `25132102`
   (412 programmes — the largest single overlap source found), is
   verified via GovTribe's own UNSPSC listing to specifically mean
   **"Military drone"**, confirmed against real ingested titles:
   "Uncrewed Aircraft System - Light", "Defence Drone Initiative
   (DDI) Marketplace", "Autonomous Mine Countermeasures (MCM)
   Uncrewed Surface Vehicle (USV)", "PUMA and RAVEN spare parts"
   (both real, named small UAS platforms). These 412 genuine
   drone/UAS tenders were counting toward Aerospace Components and
   Military Aviation while NEVER counting toward UAV/UAS at all — the
   literal cause of that sector's numbers looking undercounted while
   the other two looked inflated. Fixed with the SAME longest-
   prefix-wins mechanism `capability_resolver.py` already implements
   — no code change, only a new, more specific `25132102 ->
   UAV.INTEGRATION` mapping that wins over the broader `2513` family
   fallback for exactly this code, while a genuinely different code
   in the same family (`25131709`, confirmed via GovTribe as
   "Military transport aircraft", 74 programmes) correctly keeps
   falling back to Aviation as before.

Live-verified before/after: Military Aviation 1,618 -> 446, Aerospace
Components 2,330 -> 1,918, UAV/UAS 327 -> 739 (+412, exactly matching
the reclassified drone tenders). A dozen much smaller `2513`-family
codes (13, 13, 9, 8, 2... programmes each) could not be confidently
identified from public sources within reasonable effort and were left
on the family-level fallback rather than guessed at — same standing
discipline as migration 041 (narrow only what can be verified).
`total_programmes_resolved` (5,120) is unchanged by either fix, since
both are re-labelling which sector(s) an already-resolved programme
counts toward, not a change in how many programmes resolve at all.
Real, legitimate multi-sector overlap remains for the codes confirmed
genuinely dual above (334511's 3-way, 336411's 2-way, 541512's
2-way) — that is an honest property of the government's own
classification schemes sharing one code across real, related product
categories, not further-fixable data cleanup.

### A real, platform-wide data gap: 30,926 opportunities with no next_action, found by a user's own arithmetic not adding up (2026-09)

A user cross-checked Opportunity Dashboard totals against Report
Intel's per-product Next-Best-Action count by hand (1366 total − 668
historical ≠ 240 NBA for one product) and asked why. Investigating it
surfaced a real, platform-wide bug, not just a scoping mismatch
(though that was ALSO real — see below): CORAL-CR alone had 84 live,
non-historical opportunities with `next_action IS NULL` — genuinely
missing, not merely low-confidence. Checked platform-wide: **30,926
opportunities** across every tenant, all created **2026-08-18 to
2026-08-22**, before `initial_next_action = suggest_next_action(...)`
was wired into `/products/{id}/match-programmes`'s insert (confirmed
zero NULL rows created after 2026-08-22 — this was a one-time
historical gap, not an ongoing bug). The route's own upsert
deliberately never re-touches `next_action` on conflict (see its own
comment: "a routine re-match refreshing score/confidence must never
overwrite a suggestion a human has since acted on or manually
replaced") — correct behaviour for protecting a human's edit, but it
also meant these 30,926 rows could NEVER self-heal through ordinary
re-matching, only get worse over time as more old data accumulated.

Fixed with a one-time backfill script (`suggest_next_action`, the
same pure function every other code path already uses, run per-tenant
via `set_config('app.current_tenant', ...)` to respect RLS) that only
ever writes to rows where `next_action IS NULL` — never overwrites a
real value, same protection the original upsert comment intended.
Verified after: 0 NULL rows platform-wide (was 30,926).

**The scoping mismatch that started the investigation was also real,
separately from the bug above, and is worth remembering**: Report
Intel's Next-Best-Action count is always PER-PRODUCT (see this
module's own docstring on why), while Opportunity Dashboard's totals
are TENANT-WIDE across every product. A user's manual "total −
historical" arithmetic will never equal a single product's NBA count
unless that tenant has exactly one product — comparing the two
requires summing NBA across every one of the tenant's products first.
Low-confidence is a third, independent dimension layered on top of
both (a live, low-confidence opportunity still gets a real next
action) — it does not subtract cleanly from either total either.

**New standing safeguard, not just a one-time fix**: a user's own
follow-up request, and a genuinely good idea — this exact class of
gap (a live opportunity that could have a real suggestion, silently
missing one) is now checked and shown automatically on every report
render, not left to require a human doing arithmetic by hand again.
`_nba_coverage_summary()` in `app/product_intel_report.py` computes,
per product: total / historical / live·covered / live·gap, returned
as a new top-level `nba_coverage` field (deliberately NOT an 11th
numbered engine — the 01-10 numbering is a fixed customer-facing
contract, see this file's own docstring). The frontend's
`reportNbaCoverageCard` renders it as its own panel immediately after
the engine 10 card, colored amber with an explicit gap count when one
exists, green "No gap" otherwise — same "make the gap visible instead
of requiring a human to notice" discipline as source-health
monitoring. Deliberately scoped to the product being viewed, matching
Report Intel's own per-product design.

**Tenant-wide version, built the same day, on request**: the
Opportunity Dashboard's `loadOpportunitiesDashboard` already fetches
every one of the tenant's opportunities via `/opportunities` (with
`next_action`/`programme_stage`/`response_deadline` all already on
each row) to drive its Active/Historical tabs — no new backend
endpoint was needed. `renderNbaCoverageSummary` reuses the dashboard's
own `isClosedTender` (the exact same closure both the tab split and
this summary read from, so "historical" can never mean something
different between the two) to compute the identical four numbers
tenant-wide, in a new "Next-Best-Action Coverage" panel between Team
Visibility and the scoring explainer. Live-verified against the real
tenant this investigation started from: 1,366 total, ~668-670
historical (small variance between the Python/JS date-parse and this
verification SQL's own regex-based one, not a discrepancy worth
chasing), 0 live-with-no-action — confirming the platform-wide
backfill above genuinely closed the gap for this tenant too.

### Report Intel — a "Historical" badge on already-closed tenders (2026-09)

Direct follow-up to a real user-reported confusion: a tender scored
9/high in the Opportunity Intelligence engine (07) block but did not
appear in the Opportunity Dashboard's Active tab — correctly, since
its government stage was `contract_awarded` and its deadline had
passed, so the dashboard's own Active/Historical split (see that
entry above) had already moved it to Historical. The report itself
said nothing about this, so a high score alone read as "go bid on
this" with no hint the tender was actually history.
`app/product_intel_report.py`'s new `_historical_reason(stage,
deadline, today)` mirrors the frontend's `isClosedTender`/
`programmeStatusRemark` logic exactly (same two conditions —
`contract_awarded`/`in_service` stage, or a parsed `response_deadline`
in the past) rather than inventing a second, potentially-drifting
definition of "closed". Wired into engine 07's rows as
`historical_reason` (null when still live); the frontend renders it
as a red "⏹ Historical — <reason>" line under the tender name in both
the card preview and the "see all" modal (`reportEngineRow`, one
shared renderer for both). The block's own headline and note were
also updated to name the historical count and explicitly state that
score is about fit, not about whether a tender is still open — the
exact distinction the original confusion was about.

**Immediate follow-up, same day**: Next-Best-Action (engine 10) is
DIFFERENT in kind from engine 07 and was deliberately NOT given the
same badge treatment — a user pointed out that a "next action" on an
already-closed tender isn't useful data at all (there is no real next
step on something no longer open), unlike a score, which stays a
real, honest fact worth keeping visible. `_engine_nba` now filters
out any opportunity `_historical_reason` flags, rather than badging
it, and states the excluded count honestly in the headline/note
("240 actions standing (267 closed tenders excluded — not a live
suggestion)") instead of silently shrinking the list with no
explanation — same evidence-model discipline as everywhere else in
this file (removing data without saying so would be its own kind of
dishonesty). Live-reverified against the real tender that prompted
this (INL Pakistan UAS, SKYLARK product): present with its historical
badge in engine 07, absent entirely from engine 10.

### Shared drill-down: real tenders behind every intelligence number (2026-09)

A direct user request: modules like Procurement Intelligence show
aggregate counts ("US 403 tenders", "ROU 2 tenders") with no way to
see the actual tenders behind them — forcing a manual database check
to verify a number is real. `GET /intelligence/programmes/browse`
(`app/main.py`) is ONE shared, filterable endpoint (`country`,
`organization_id`, `winner_organization_id` — joined via
`contract_awards`, `source_name`, `stage`) rather than a bespoke route
per module, so every drill-down reads the exact same `programmes`
rows each module's own aggregate was already computed from — the
numbers can never drift from what a click reveals. Deliberately
selects NO `contact_*` columns at all (not merely omits them from the
response) — `programmes.contact_*` is under the same hard scope limit
established for Tender Briefing (migration 029): read only via the
ONE programme/opportunity it belongs to, never listed or aggregated
across tenders, and a browse view is exactly the aggregation that rule
exists to prevent. Also applies `not_a_test_fixture()`, and caps
`limit` at 500 with a real `total` count alongside the (possibly
smaller) `returned` count so a UI can honestly say "showing the 500
most recent of 3,926" rather than silently truncating.

Wired in as a shared `openProgrammeBrowseModal(filters, title)` +
`wireDrillDownClicks(container)` pair on the frontend:

- **Procurement Intelligence** — every stage cell (overall funnel and
  per-source funnel) and every Top Countries row is now clickable.
- **Market Intelligence** — the country detail page gained a real
  "Tenders" list section (previously only had stage/capability
  aggregates, no way to see the actual tenders — the literal gap this
  request named).
- **Competitor Intelligence / Partner Matching** — the award-count
  cell for each company now opens its real awards, via
  `winner_organization_id`.

**Customer Intelligence and OEM Intelligence did NOT need this** —
checked their existing detail routes first rather than assuming a gap
existed everywhere: `GET /intelligence/customers/{id}` and
`GET /intelligence/oems/{id}` already return a full tenders/awards
list per organization, wired into their own "View →" detail pages
since before this session. Adding a second, redundant drill-down path
there would have been the same kind of parallel-definition risk this
project already avoids elsewhere (see capability_resolver.py's own
docstring on why Sector Coverage reuses it instead of reimplementing
resolution).

### Three real bugs found by a user actually using the platform (2026-09)

**Home vs Presentation showed different "Live Tenders Tracked"
numbers — real bug, fixed at the root.** `/public/platform-stats`
(Home) has always excluded test-suite fixture rows via
`not_a_test_fixture()`; `get_procurement_funnel` (feeding
`/intelligence/procurement`, which Presentation reads live) never
did, on the stated reasoning that fixture pollution there was
"harmless for the authenticated Procurement Intelligence tab" — true
right up until Presentation started reading a live number from it
during sales demos. Live-confirmed: 658 "Test Fixture: ..." rows out
of 10,558 total. `not_a_test_fixture` moved out of main.py into its
own tiny module, `app/test_fixture_filter.py` (same "no sqlalchemy at
import time, avoid circular imports" pattern as
`address_format.py`/`org_name_format.py` — `get_procurement_funnel`
lives in a module main.py itself imports, so it couldn't import the
predicate back from main.py), and `get_procurement_funnel` now
applies it. Both endpoints report 9,900 after the fix; a regression
test (`test_procurement_total_matches_public_platform_stats`) pins
this so the two can never silently drift apart again.

**Taxonomy Admin now shows real mapping/programme/contract-award
counts per capability** — a fair question after migration 041 deleted
8 of NAICS 334511's mappings: how do you actually SEE the effect of a
mapping change, instead of asking for a manual database check every
time? `app/sector_coverage.py`'s new `capability_contribution()`
(same code→capability resolution as Sector Coverage, at capability
rather than sector granularity) computes, per capability:
`mapping_count` (NAICS+CPV+UNSPSC rows pointing to it — the thing a
migration like 041 actually edits), `programme_count` (real ingested
programmes currently resolving to it), and `contract_award_count`
(real awards against those programmes — genuine evidence of OEMs
winning work there, not just tenders existing). Wired into
`GET /admin/taxonomy`, shown as three new columns in the Taxonomy
Admin table. Same legitimate double-counting as Sector Coverage: a
code mapped to several capabilities means a programme's count is
added to each one, by design.

**A real, partially-fixed data-freshness gap, found investigating a
specific user report (product "CORAL-CR", tender briefing missing a
contact SAM.gov's own site shows).** Traced to real stale data, not a
extraction bug: `record_programme`'s upsert already correctly
refreshes `contact_*` fields `ON CONFLICT ... DO UPDATE` (verified by
reading the SQL) — so a re-ingested notice DOES get fresh contact
data. The actual problem is that many real SAM.gov programmes simply
haven't been RE-fetched since the 2026-09-18 fix (limit 1000 +
per-NAICS offset rotation): live-confirmed 539 genuine (non-test-
fixture) SAM.gov programmes across 23 NAICS codes still carried
pre-fix, empty contact data as of 2026-09-21. A manual backfill
across those 23 codes refreshed 3,371 records (500 new awards) before
hitting SAM.gov's own daily quota on the remaining 11 (small-volume)
codes — those will self-heal via the normal scheduled rotation.

**A second, deeper, NOT-yet-fixed issue surfaced while backfilling
NAICS 334511 specifically**: even after two explicit re-ingestion
runs targeting it, 158 of its stale rows remained untouched. Leading
hypothesis, not yet live-verified (SAM.gov's quota was exhausted
mid-investigation): `days_back` filters by the notice's *posted*
date (`postedFrom`/`postedTo`), not by whether it's still open. A
notice posted many months ago with a still-future
`response_deadline` can sit permanently outside any reasonable
`days_back` window and never get re-touched by ANY future run,
regardless of the offset/rotation fix — a genuinely different problem
from the one fixed on 2026-09-18. Worth a proper fix (e.g. a periodic
"refresh programmes with a still-future response_deadline regardless
of posted date" pass) rather than another one-off manual backfill;
not attempted here since it needs live verification against a reset
quota first.

### Top navigation regrouped + Architecture Map cleanup (2026-09)

A second, separate user complaint after the horizontal-scroll pass
above: the top nav's ~17 mode tabs, even once wrapped instead of
side-scrolling, put six pure-explainer pages (Platform, Architecture
Map, Engines, How It Works, Solutions, Global Markets) physically
between `My Products` and `Opportunity Intel` — breaking the actual
working rhythm (add a product → match → work the opportunity → check
the report) while using or demoing the app. `MODES` (the single array
driving both the desktop tab row and the mobile `<select>`) now
carries an optional `group` field; anything without one stays a
direct tab in work-path order (Home, My Products, **Industries**,
Opportunity Intel, Report Intel, Presentation, plus Taxonomy Admin
when `is_platform_admin`), and `group:'explore'` / `group:'company'`
items render inside one of two `<details>` dropdowns
(`MODE_DROPDOWNS`) instead. Industries stays a direct tab
deliberately, unlike the other five — see its own Sector Coverage
entry above: it shows the tenant's real, live per-tenant opportunity
counts, not marketing copy, so it belongs with the working pages, not
the explainer ones. `renderTopControls()` closes a dropdown on both
an item click and an outside click (`<details>` has no native
"close on outside click"); the outside-click listener is bound once
via a `window._modeDropdownOutsideClickBound` guard, not re-added on
every `render()` call.

**The Architecture Map's connector lines were only partly straight
verticals, not the raw diagonals first assumed** — checked the actual
rendering code before touching it, rather than guessing from how the
diagram looked: every edge with an explicit `anchorX` override was
already vertical (both endpoints forced to the same x specifically to
avoid overlapping arrows converging on a wide box). The genuinely
diagonal ones were the 10-way source-row fan-in into `INGESTION AND
NORMALIZATION` (each source box's own center to the ingestion box's
center) and two facilitation-layer edges (`Engagement Intel` →
`Path to Contract`, `Opportunity Scoring` → `Company Profile`) whose
endpoints were never given a shared x at all. `renderArchitectureSVG`
now emits a straight `<line>` only when both ends already share an x,
and an orthogonal (L-shaped) `<path>` elbow — bending halfway down the
vertical gap — for every other edge, the standard flowchart-connector
shape instead of an ad-hoc diagonal.

**Advance planning for the next data source, done now rather than
deferred**: the ten source boxes used to be ten separately hand-typed
`{x, y, w, h, ...}` node objects — adding an eleventh would have meant
manually recalculating every box's x and the total row width by hand.
`ARCH_SOURCES` is now a plain list of just the source's own facts
(title/sub/color/`sourceFullName`), and `ARCH_SOURCE_NODES` computes
x/y/w for all of them from `SOURCE_ROW_X`/`SOURCE_ROW_W`/
`SOURCE_ROW_GAP` and the list's own length — adding source #11 is a
one-line addition to `ARCH_SOURCES`, nothing else in this file needs
touching, and `ARCH_EDGES`' fan-in is generated from the same list so
the new source's arrow into `INGESTION AND NORMALIZATION` is wired in
for free. This is layout-only preparation — it does not add an
eleventh live government data source itself, which is real backend
ingestion work (see how every other `### <Country> — Nth real source`
entry above was actually built) and was deliberately not attempted in
the same pass as a frontend nav/diagram cleanup.

### Source-health monitoring (2026-09) — the fix for a real, already-happened incident

`GET /ingestion/sources/status` used to report only scheduler
config (next run time, whether a key is configured) — no run
HISTORY. The only "last run" signal the frontend had was
`/ingestion/jobs`, a GLOBAL `limit 20` feed across every source
ordered by recency. That is the exact mechanism that let eTenders
South Africa go silently dead for 3 weeks (2026-08-24 to 2026-09-15,
see that source's own entry above) before a human noticed by hand —
a source running on a slower cadence than SAM.gov's can be pushed
out of the most-recent-20 window entirely within a day or two, at
which point the UI just shows "—", indistinguishable from "never
configured".

Fixed by computing each source's OWN `last_success_at` (max
`finished_at` where `status='succeeded'`) and own most recent attempt
(`last_run_status`/`last_run_at`/`last_run_error`) directly from
`ingestion_jobs` grouped by source — never sharing the `limit 20`
window, so a quiet source can no longer hide behind a busy one.
`health` is a plain three-state field: `never_run` (no job row
exists at all), `stale` (a success exists but is older than
`health_threshold_hours`), or `healthy`. The threshold is
`max(48, interval_hours * 3)` — tolerates up to two missed scheduled
cycles before flagging, with a 48h floor so a frequently-scheduled
source isn't flagged over one slow run — a stated, explainable rule,
not a guessed number. `/ingestion/jobs` itself is untouched (still a
useful raw recent-activity feed); this is a second, source-scoped
query added alongside it.

Frontend: the Ingestion Control table (Taxonomy Admin →
Ingestion Control) gained a Health column (colored dot + label, with
a title tooltip explaining the specific hours-since/threshold
numbers behind it) and a summary banner above the table — a single
"✓ All sources healthy" line, or a specific "N sources stale / N
never run" warning naming the count, computed from the same response
so it can never drift out of sync with the per-row badges below it.
This is deliberately NOT a separate alerting/paging system (no email,
Slack, or external monitoring integration exists) — it makes the
existing state visible to whoever opens this page, which is still a
real improvement over requiring a human to manually query
`ingestion_jobs` by hand (which is literally how the South Africa
outage was originally found), but someone still has to open the page.
A real always-on alert (see the ranked recommendation list this was
built from) is a separate, larger piece of work not attempted here.

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

**`"null"` is deliberately in the allow-list too** (2026-09-23, real
user-reported bug) — not a wildcard, the literal `Origin: null` header
a browser sends for a page opened directly from disk (`file://`),
which is how this project's own single-file frontend is normally
opened (double-click, no local server). Without it, EVERY `fetch()`
from that file:// page was silently blocked by the browser as a CORS
failure ("Failed to fetch") — including a password-reset submission,
which is why resetting the password didn't fix the user's login
either; the request never reached the API at all. Confirmed live with
a real `OPTIONS`/`POST` carrying `Origin: null` before and after the
fix (`curl -H "Origin: null"`, checking for
`access-control-allow-origin: null` in the response).

### Customer & OEM Intelligence (`app/customer_intelligence.py`,
`app/oem_intelligence.py`, `app/capability_resolver.py`)

The original roadmap's Phase 4, deliberately deferred in favor of
Opportunity Intelligence (see README), later revisited. Both are
pure aggregation over data already ingested — no new trust/evidence
questions for Customer Intelligence, which groups `programmes` by
buyer (`organizations.org_type = 'government_body'`).

OEM Intelligence needed one real schema addition: `contract_awards`
(migration 016), because an award names a DIFFERENT organisation
(the winner, `org_type = 'oem'`) than the one already on the
programme (the buyer) — and, confirmed against live TED data, a
single award notice can name several winners at once (one per lot),
so it's genuinely one-to-many.

Winner extraction now covers **5 of 9 sources** (TED, UK Find a
Tender, Colombia, ProZorro, CanadaBuys) — 2,887 real awards live.
CanadaBuys and SAM.gov were investigated in a 2026-09 Report Intel
follow-up and are worth knowing about specifically:

- **CanadaBuys award data lives in a genuinely SEPARATE file** from
  the open-tenders CSV this platform already ingests —
  `{fy}-awardNotice-avisAttribution.csv`, published per Government of
  Canada fiscal year (April 1 – March 31; `current_fiscal_year_label`
  computes which one), found via open.canada.ca's dataset page, not
  guessed at. Confirmed live: same column-name scheme as the
  open-tenders file for buyer/solicitation/UNSPSC, so
  `is_defence_buyer`/`parse_unspsc` are reused unchanged — but it adds
  its own genuinely richer contact block (a full postal address,
  which the open-tenders file does not have at all) and a
  `supplierLegalName` field the open-tenders file also lacks.
  `run_canada_buys_ingestion` now fetches BOTH files as two phases in
  one run; the award phase's own failure is recorded but never rolls
  back the open-tenders phase, which already committed. First live
  run: 4,671 award rows examined, 1,492 kept, 1,227 real awards
  recorded — the single largest jump of any source. One `awardStatus`
  value (`Cancelled`, 8 of 3,771 rows in a live sample) is skipped as
  real history that never had a winner; `Expired` is kept (the
  contract term ended, which is not the same as the award being
  invalid).
- **SAM.gov's `award.awardee.name` field has been fetched since day
  one and never once read** — a real, plain gap, not a missing
  source. Both of GSA's own documented examples were used to build
  `extract_award_winner`, including the awkward one: "Example 2"
  shows `award` present with date/number/amount but its `awardee`
  sub-object ABSENT ENTIRELY (not null — simply not a key), which
  `.get()`-chaining handles without assuming the shape. No winner
  country field exists on this object at all, so `winner_country` is
  passed as `None` honestly rather than assumed to be the United
  States. **Live-confirmed 2026-09-17, once the daily quota reset**: a
  triggered run (`days_back=90`) queried NAICS `541512`/`811210` —
  this session's own Phase 2 rotation additions — via
  `used_automatic_rotation`, ingested 200 programmes and recorded 30
  real contract awards with genuine winner names (e.g. "L1
  ENTERPRISES INCORPORATED", "AGE Cyber Defense Solutions, LLC"),
  confirming `extract_award_winner` works against live data, not
  just GSA's fixtures. Also confirmed the Phase 2 NAICS expansion
  itself closes the loop it was meant to: all 200 newly-ingested
  programmes resolve correctly through `taxonomy_naics_mapping` — 100
  rows on `541512` resolving to both `C4ISR.INTEGRATION` and
  `CYBER.DEFENCE` (a real one-code-to-two-capabilities case, same
  pattern as CPV 35400000 documented in `capability_resolver.py`'s
  own docstring), 100 rows on `811210` resolving to `MRO.GENERAL`.
  Note while checking this: `programmes.capability_required` is a
  schema column no application code anywhere reads or writes — actual
  capability resolution always goes through the
  `naics_code`/`cpv_code`/`unspsc_prefix` mapping tables at query
  time (`capability_resolver.py`, `programme_matching.py`), never a
  column stored on the row itself; don't be misled into thinking a
  NULL there means resolution failed.
- **CPPP and South Africa remain unaddressed**: CPPP is HTML-scraped
  with no structured award field to extract from; South Africa's OCDS
  feed carries no award-stage data in what's been sampled live so
  far. Left as stated, honest gaps rather than guessed at.

TED's winner data has a real irregularity worth knowing before
touching it: `winner-name` and `winner-country` are parallel arrays,
but the name list routinely contains duplicates (the same company
listed once per lot it won) and — even after deduplication — the
name and country counts don't always match (confirmed: 8 of 9 live
notices sampled aligned exactly, one didn't). `extract_winners`
therefore only attaches a country when the deduplicated name count
equals the country count; otherwise every winner in that notice gets
`country: None` rather than a guessed pairing.

`capability_resolver.py` is the shared piece both intelligence
modules depend on — it resolves a programme's raw classification
code back to a capability label, checking NAICS/CPV by exact match
first, then UNSPSC by longest-prefix match. It deliberately imports
`sqlalchemy` only inside its I/O function, not at module level, so
its pure `resolve_capability` function stays importable (and
unit-testable) in the local dev venv, which has no `sqlalchemy`
installed — matching every other pure normalizer module.

### Engagement Intelligence, Next-Best-Action, Procurement &
Competitor Intelligence (`app/next_best_action.py`,
`app/procurement_intelligence.py`, `app/competitor_intelligence.py`,
the `PATCH /opportunities/{id}` route)

The remaining four items of the original roadmap's Phase 4. Like
Customer/OEM Intelligence, these needed **no new schema** —
`opportunities.stage` (a full sales-pipeline enum), `next_action`,
`owner_user_id` and `due_date` all existed since the Phase 0 schema
and were written to by nothing until this. `audit_log`'s own column
comment even names the action this now logs
(`'opportunity.stage_changed'`) — the pipeline was designed for this
from the start.

Two real bugs worth knowing if you touch this code again:
- **asyncpg needs a real `date` object for a `date` column** — a raw
  `text()` query bound to a plain `'YYYY-MM-DD'` string fails with an
  opaque `'str' object has no attribute 'toordinal'` `DBAPIError`, not
  a clean validation error. `date.fromisoformat()` the value before
  binding it, same as `update_opportunity` does.
- **A no-op `PATCH` must not write an audit log entry.** The
  frontend deliberately sends an empty `PATCH {}` to *read* an
  opportunity's current state (there's no dedicated GET-single
  route) — logging that as a real engagement event would pollute
  the history timeline with entries representing nothing happening.
  `update_opportunity` compares before/after state and only calls
  `write_audit_log` when something actually changed.

`next_best_action.py` is deliberately rules-based, not ML — matching
this whole project's stance (see `capability_taxonomy` and
`programme_matching`, both scored rules a human confirms, never a
black box). A stage change with no explicit `next_action` in the
same request gets a fresh suggestion computed for the *new* stage;
an explicit `next_action` always wins.

`procurement_intelligence.py` aggregates `programmes.stage` — the
**government's** procurement lifecycle for the tender itself.
Deliberately distinct from `opportunities.stage`, the **tenant's**
own sales pipeline for one product against one programme; don't
conflate the two when extending either.

`competitor_intelligence.py` is the one genuinely tenant-scoped
module in this whole group (Customer/OEM/Procurement Intelligence
are shared reference data, identical for every tenant). "Competitor"
means: a real company confirmed winning an award
(`app/oem_intelligence.py`'s data) in a capability *this tenant* has
analyst-confirmed for one of their own products — reusing the exact
same confirmed-capability query `programme_matching.py` uses for
matching, so "your competitors" and "what you'd match against" never
drift apart into two different notions of "your capabilities."

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
