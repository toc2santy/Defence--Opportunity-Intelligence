# Defence Opportunity Intelligence — Engineering Status

This document reflects what's actually built and verified, not an
aspirational brief. Every claim below has been run against a real
database, a real API, and — where noted — a real external government
data source. Known gaps and limitations are stated plainly rather
than glossed over; that discipline is what makes the rest of this
document trustworthy. Full technical narrative and reasoning for
everything summarized here lives in `CLAUDE.md`, in chronological,
dated entries — this file is the current-state summary, that one is
the "why" and "how it was found" history.

Two real tenants use this platform today. This is no longer just a
local prototype.

## At a Glance

| Area | Status |
|---|---|
| Multi-tenant foundation (schema, auth, RBAC, RLS, audit log) | ✅ Built, tested |
| Government data sources (10, real, live) | ✅ Built, tested, live-verified |
| Programme matching (NAICS + CPV + UNSPSC, keyword-corroborated) | ✅ Built, tested, self-corrected multiple times on real evidence |
| Capability taxonomy + human-in-the-loop confirmation | ✅ Built, tested |
| Eligibility/set-aside fields (descriptive, not legal advice) | 🟡 Wired for 5 of 10 sources — the rest genuinely don't publish one, or it's not yet built |
| Intelligence engines (Customer/OEM/Competitor/Market/Partner/Procurement/Engagement/NBA/Report) | ✅ Built, tested, live-verified |
| Scheduled ingestion + "what's new" | ✅ Built, tested |
| MFA (TOTP + backup codes) | ✅ Built, tested, live-verified |
| Per-account failed-login lockout | ✅ Built, tested, live-verified |
| Session revocation (instant, on password change/block/self-service) | ✅ Built, tested, live-verified |
| Platform Admin — user management, block/unblock, activity log | ✅ Built, tested, live-verified |
| Encryption at rest (MFA secrets, PII, backups) | ✅ Built, tested, live-verified |
| Security response headers (CSP, HSTS, nosniff, etc.) | ✅ Built, tested |
| Backup & DR — encrypted daily backups, continuous WAL archiving, automated restore drills, on-demand point-in-time recovery | ✅ Built, tested, live-verified (including a real incident recovery) |
| Isolated test stack (tests no longer touch real data) | ✅ Built, tested |
| **Cross-tenant Partner/JV Matching (monetizable)** | 🟡 Designed, not built |
| **Monetization / billing** | 🟡 Designed, not built |
| **Automated eligibility verdicts (beyond descriptive)** | ❌ Deliberately not built — real legal liability, needs counsel first |
| **Standby replica / geographic failover (Backup Phase 3)** | 🟡 Deferred — needs a second paid server, the user's own budget decision |
| **Always-on production deployment** | ❌ Still local Docker — the scheduler only runs while the API process does |

626 automated tests. On a completely bare, freshly-migrated database
(no real government data ever ingested into it), 617 pass — the
other 9 either need real ingested award/programme data no fresh
install has yet (5, all a real, stated limitation, not a bug — see
`CLAUDE.md`'s isolated-test-stack entry), or are deliberately opt-in
live-external-API tests that skip without a real credential (7).

## Government data sources (10, real, live)

| Source | Country | Classification scheme |
|---|---|---|
| SAM.gov Contract Opportunities API | United States | NAICS |
| UK Find a Tender Service | United Kingdom | CPV |
| EU TED (Tenders Electronic Daily) | European Union | CPV |
| CPPP (Central Public Procurement Portal) | India | Organisation-sentinel + keyword |
| CanadaBuys | Canada | UNSPSC (prefix-matched) |
| eTenders South Africa | South Africa | Organisation-sentinel + keyword |
| SECOP II (Colombia Compra Eficiente) | Colombia | NAICS-adjacent |
| ProZorro | Ukraine | CPV |
| AusTender | Australia | UNSPSC |
| DNCP Paraguay | Paraguay | UNSPSC |

Each source has its own `*_normalize.py` (pure logic, unit-tested
against real captured API responses, no network) and `*_ingestion.py`
(the actual fetch + upsert orchestration) module. Every source was
verified against its real live API before being trusted — several
early assumptions were caught and corrected this way (a wrong URL
parameter, a truncated-response retry gap, a false-positive
eligibility phrase, a case-folding bug), documented in `CLAUDE.md`
rather than silently fixed and forgotten.

**Eligibility/set-aside fields** (a bidder-restriction signal, e.g.
"Small Business Set-Aside," descriptive only — never a legal
eligibility verdict) are wired for SAM.gov, TED, CanadaBuys, DNCP
Paraguay, and UK Find a Tender. The other 5 sources were individually
checked and either genuinely don't publish anything of the kind
(confirmed live, not assumed), or the field exists but was never
observed populated in real sampled data.

## Intelligence engines

All real, live-verified against real ingested data — not the original
prototype's hash-based fake scores:

- **Capability Intelligence** — weighted keyword taxonomy, human-confirmed, evidence-backed.
- **Market Intelligence** — every real source above.
- **Programme Intelligence** — NAICS/CPV/UNSPSC + keyword-corroborated matching, self-corrected multiple times against real false positives (see `CLAUDE.md`'s "UAV Kumbhigram" entry for the most recent).
- **Customer Intelligence** — every real buyer across every source.
- **OEM & Partner Matching** — OEM: real award winners. Partner: shared-buyer count, not an invented percentage.
- **Competitor Intelligence** — tenant-scoped, analyst-confirmed capability areas only.
- **Opportunity Intelligence** — the direct output of Programme Intelligence matching, plus Fit & Feasibility Score.
- **Procurement Intelligence** — funnel view by source/country/stage.
- **Engagement Intelligence** — pipeline management, audit-logged, Next-Best-Action per opportunity.
- **Product Intel Report** — a consolidated, per-product narrative across every engine above.

## Account security

- **MFA** — TOTP (any authenticator app), 10 one-time backup codes (download/print/copy), enrolled via Company Profile's own "Two-Factor Authentication" panel.
- **Per-account failed-login lockout** — 5 wrong passwords locks the account for 15 minutes (both configurable), independent of which IP the attempts came from — the complement to IP-based rate limiting.
- **Session revocation** — every access token carries a version claim checked on every request; a password change, an admin block, or self-service "Sign Out Everywhere" invalidates every existing session instantly, not just future logins.
- **Self-service change-password** and **admin-triggered password-reset links** (the admin never sees or sets the actual new password).
- **Platform Admin — User Management** (Company Profile's own bottom panel, platform-admin-gated): search/list every real account across every tenant, reset-password, block/unblock, and a per-user activity log built on the audit trail every write route already produces.
- **Encryption at rest** — MFA TOTP secrets, and a tenant's own GST/PAN/TAN/IEC registration numbers, each under its own dedicated Fernet key (never reused across purposes — a leak of one key never compromises an unrelated secret).
- **Security response headers** — `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security`, `Content-Security-Policy` — applied to every response by one middleware.

## Backup & Disaster Recovery

- **Phase 1** — daily encrypted `pg_dump` to Cloudflare R2.
- **Phase 2** — continuous WAL archiving (5-minute `archive_timeout`) to the same bucket, the foundation for point-in-time recovery.
- **Phase 4** — automated weekly restore drills (a real `pg_restore` into a scratch database, row counts compared against live) and staleness/failure alerting.
- **On-demand PITR** (`db/pitr_restore.sh`) — restores to any specific timestamp within the WAL-archived window, live-verified with genuine second-level precision (a real transaction committed one second after the target time was correctly excluded from the restored instance).

This system had its first real, non-drill use in production: a real
operator mistake (a mis-scoped bulk DELETE, root-caused to a SQL
subquery footgun — see `CLAUDE.md`) wiped tenant-owned data
platform-wide, and was fully recovered from a real backup with no
permanent data loss. The full incident writeup, root cause, and the
resulting permanent precaution checklist are in `CLAUDE.md`.

## Isolated test stack

`docker-compose.test.yml` — a genuinely separate Postgres + API pair,
so `pytest` no longer writes real throwaway signups into the same
database real tenants use. This closed a real, expensive gap: two
separate cleanups this project's own history had to delete 8,155 and
then 1,383 accumulated test tenants by hand before the isolation
existed.

```bash
docker compose -f docker-compose.test.yml up -d --build
for f in $(ls db/migrations/*.sql | sort); do
  docker compose -f docker-compose.test.yml exec -T db-test psql -U postgres -d doi -f - < "$f"
done
docker compose -f docker-compose.test.yml exec api-test alembic stamp head
```

See `CLAUDE.md`'s "Isolated test stack" entry for why `alembic
upgrade head` alone doesn't work on a genuinely fresh database in
this project (a real, separate async-driver limitation, not specific
to this stack).

## Architecture

**Database**: PostgreSQL. Tenant-owned data (`products`,
`opportunities`, `product_capabilities`, `audit_log`) is isolated via
Row-Level Security, enforced at the database connection level, not by
application code remembering a `WHERE tenant_id = ...` clause. Shared
reference data (`programmes`, `organizations`, `capability_taxonomy`,
`sources`, `evidence`) has no tenant scoping by design — every tenant
benefits from the same ingested market/programme intelligence.

**API**: FastAPI, JWT auth (with per-user session-version revocation),
Argon2 password hashing, raw SQL via SQLAlchemy Core `text()` — no
ORM, so every migration is hand-written rather than autogenerated
from a model diff.

**Evidence model**: nothing in this system claims a fact without a
traceable source. Every classification, every ingested programme,
every match carries a `source_id`, a `confidence` level, and an
`evidence_status`. Enforced by schema, not just UI copy.

**Human-in-the-loop**: AI-suggested capability classifications are
never used for programme matching until an analyst explicitly
confirms them — enforced in the matching query itself.

## Running it locally

```bash
cp .env.example .env
# fill in JWT_SECRET, MFA_ENCRYPTION_KEY, PII_ENCRYPTION_KEY, BACKUP_ENCRYPTION_KEY
# (generate each with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#  except JWT_SECRET: python3 -c "import secrets; print(secrets.token_urlsafe(48))")
# leave SAM_GOV_API_KEY / R2_* blank unless you're running live ingestion or backups

docker compose up --build
# API on http://localhost:8000, Postgres on localhost:5432
```

**A genuinely fresh database** cannot use `alembic upgrade head`
alone past migration `0001_baseline` (see `CLAUDE.md`) — apply every
`db/migrations/*.sql` file in order via `psql`, then
`docker compose exec api alembic stamp head`, the same pattern the
isolated test stack above uses.

To become a platform admin (required for User Management, Taxonomy
Admin, and every cross-tenant route):

```bash
docker compose exec db psql -U postgres -d doi -c "update users set is_platform_admin = true where email = 'you@example.com';"
```

## Testing

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

Requires the isolated test stack (above) running — `tests/conftest.py`
defaults to it, not the real dev stack. Split into pure unit tests (no
DB, no network — scoring/normalization/matching logic itself) and
integration tests (hit the real running stack over HTTP, no mocking).
This split has repeatedly caught real bugs a mocked-DB test would not
have: DB permission grants, a jsonb serialization quirk, a SQLAlchemy
cast-parsing gotcha, a UUID type mismatch, and — this session — the
SQL subquery footgun behind the incident described above.

## Known limitations — stated plainly, not hidden

- **India market coverage beyond CPPP is thin.** CPPP itself has no
  real classification scheme — relevance there is organisation-
  sentinel + keyword corroboration, not a real category match.
- **Eligibility fields are descriptive only, by deliberate design.**
  This platform never determines whether a company can legally bid on
  something. A real automated eligibility verdict was explicitly
  planned against, not just deferred — the liability risk of a wrong
  automated "you can/can't bid" signal was judged not worth it.
- **The scheduler only runs while the API process does.** No
  always-on deployment exists yet — "scheduled ingestion" is real in
  code but not continuously active outside a running `docker compose`
  session.
- **Cross-tenant Partner/JV matching and monetization are designed
  (mutual opt-in reveal, tiered pricing, a non-negotiable "never tune
  matching on revenue" rule) but not built** — no code exists for
  them yet.
- **Backup Phase 3 (standby replica / geographic failover) is
  deferred** — needs a second paid server, a budget decision that
  belongs to the person running this platform, not an engineering
  gap.
- **The Postgres passwords (`doi_app`, `postgres` superuser) are
  still hardcoded** in `db/roles.sql`/`docker-compose.yml` —
  acceptable only because the database port is not reachable outside
  this local-only setup; must be fixed before any deployment where
  that changes.

## Why RLS instead of just filtering in application code

A `WHERE tenant_id = :tenant_id` clause in every query works right up
until one route forgets it — and in a codebase with hundreds of
routes, that's a `when`, not an `if`. Postgres RLS makes the tenant
boundary a property of the database connection itself: even a buggy
or malicious query literally cannot return another tenant's rows,
because Postgres filters them out before the application ever sees
them. Verified both by hand and by the automated test suite on every
run.
