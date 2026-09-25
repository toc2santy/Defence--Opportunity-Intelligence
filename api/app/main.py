"""
Defence Opportunity Intelligence — API foundation.

Phase 0, revised after first live run: reads real config from the
environment (was hardcoded before), and adds the signup/login
routes that were missing — without these, there was no way to
create a tenant/user or obtain a real token to test anything
beyond /healthz.
"""

import hashlib
import httpx
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from urllib.parse import urlencode, quote
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.classification import classify_text, MINIMUM_SCORE_TO_SUGGEST
from app.sam_gov_ingestion import run_sam_gov_ingestion, IngestionConfigError, get_rotation_index
from app.uk_ft_ingestion import run_uk_ft_ingestion
from app.ted_eu_ingestion import run_ted_eu_ingestion
from app.cppp_india_ingestion import run_cppp_india_ingestion
from app.canada_buys_ingestion import run_canada_buys_ingestion
from app.south_africa_ingestion import run_south_africa_ingestion
from app.colombia_ingestion import run_colombia_ingestion
from app.australia_ingestion import run_australia_ingestion
from app.paraguay_ingestion import run_paraguay_ingestion
from app.prozorro_ingestion import run_prozorro_ingestion
from app.sam_gov_normalize import DEFENSE_RELEVANT_NAICS, get_naics_group, NAICS_GROUP_SIZE
from app.ingestion_common import IngestionSourceConfig, run_scheduled_source
from app.programme_matching import match_product_to_programmes, preview_match_for_text
from app.customer_intelligence import list_customers, get_customer_detail
from app.sector_coverage import sector_coverage, capability_contribution
from app.backup import (
    run_backup, backup_health, BackupConfigError,
    run_restore_drill, restore_drill_health, check_staleness_and_alert,
)
from app.retention import run_retention_purge, retention_health
from app.oidc import (
    get_provider_config, discover_provider, make_pkce_pair, make_state_token,
    decode_state_token, exchange_code_for_tokens, verify_id_token,
    OidcConfigError, OidcVerificationError,
)
from app.oem_intelligence import list_oems, get_oem_detail
from app.next_best_action import suggest_next_action
from app.procurement_intelligence import get_procurement_funnel
from app.test_fixture_filter import not_a_test_fixture
from app.product_intel_report import build_product_intel_report
from app.market_intelligence import list_markets
from app.competitor_intelligence import list_competitors
from app.partner_matching import list_partners
from app.credential_status import compute_status
from app.pii_encryption import encrypt_field, decrypt_field, PiiConfigError
from app.email_sender import send_email
from app.mfa import (
    MfaConfigError, generate_totp_secret, encrypt_secret, decrypt_secret,
    provisioning_uri, verify_totp, generate_backup_codes, hash_backup_code, verify_backup_code,
)

# ---------------------------------------------------------------
# Config — now actually read from the environment docker-compose
# sets, with sane local-dev fallbacks. Previously these were
# hardcoded literals that silently diverged from docker-compose.yml.
# ---------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://doi_app:changeme@localhost:5432/doi"
)
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-real-deploys")
JWT_ALGO = "HS256"
ACCESS_TOKEN_MINUTES = 30

# How often the scheduled SAM.gov pull runs, in hours. Kept short
# enough to test in a dev cycle (SCHEDULED_INGESTION_INTERVAL_HOURS
# env var), defaults to a real daily cadence in normal use.
SCHEDULED_INGESTION_INTERVAL_HOURS = int(os.environ.get("SCHEDULED_INGESTION_INTERVAL_HOURS", "24"))

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
ph = PasswordHasher()

app = FastAPI(title="Defence Opportunity Intelligence API")
bearer_scheme = HTTPBearer()

# ---------------------------------------------------------------
# Rate limiting. A global default (generous — this is protection
# against runaway scripts/DoS, not a throttle on normal use)
# applies to every route automatically via the middleware. Specific
# routes that genuinely warrant tighter limits (signup, login,
# triggering external ingestion) are decorated individually below.
#
# Keyed by client IP. Limits are env-configurable so a real
# deployment can tighten them without a code change — the defaults
# here are dev-friendly enough that this project's own ~25-30
# test-suite signups per run don't trip them, while still being a
# real, meaningful limit against actual abuse (a legitimate shared
# office IP doing dozens of real signups an hour is normal; a
# script doing thousands is not).
# ---------------------------------------------------------------
DEFAULT_RATE_LIMIT = os.environ.get("DEFAULT_RATE_LIMIT", "300/minute")
SIGNUP_RATE_LIMIT = os.environ.get("SIGNUP_RATE_LIMIT", "100/hour")
LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "200/hour")
INGESTION_RATE_LIMIT = os.environ.get("INGESTION_RATE_LIMIT", "5/hour")
# The one un-authenticated route that touches real data (the homepage
# preview widget) — tighter than DEFAULT_RATE_LIMIT specifically
# because it needs no login, so it's the one endpoint a script could
# hammer without ever creating an account.
PREVIEW_RATE_LIMIT = os.environ.get("PREVIEW_RATE_LIMIT", "20/minute")
# Tight, deliberately — a forgot-password request triggers an email
# send (or, in dev, a console log) per hit, and the route is one of
# the few that must respond identically whether or not the address
# exists, so it can't lean on "that email isn't registered" to cut a
# request short cheaply.
PASSWORD_RESET_RATE_LIMIT = os.environ.get("PASSWORD_RESET_RATE_LIMIT", "5/hour")

# Per-ACCOUNT failed-login lockout (2026-09) — the complement to
# LOGIN_RATE_LIMIT above, which is keyed by IP and so cannot stop a
# distributed credential-stuffing attempt (many IPs, one targeted
# account) or a single attacker rotating IPs. This locks the specific
# account after too many wrong passwords in a row, independent of
# where the attempts came from.
MAX_FAILED_LOGIN_ATTEMPTS = int(os.environ.get("MAX_FAILED_LOGIN_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))

limiter = Limiter(key_func=get_remote_address, default_limits=[DEFAULT_RATE_LIMIT])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ---------------------------------------------------------------
# CORS. Restricted to the actual frontend origin(s), not wide open
# — a browser calling this API from any other origin would be
# silently blocked without this, and blindly allowing "*" would
# defeat the point of having a real allow-list at all once
# credentials (the JWT in the Authorization header) are involved.
# ---------------------------------------------------------------
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5500")
# This API's own public base URL — needed for SSO (2026-09): the
# redirect_uri an OIDC provider sends a user back to after login must
# be this API's own /auth/oidc/{provider}/callback, and must exactly
# match what's registered with that provider (Google/Microsoft both
# reject a mismatched redirect_uri outright, a real anti-hijack
# control on their side, not this app's own).
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
app.add_middleware(
    CORSMiddleware,
    # "null" is not a wildcard — it's the LITERAL Origin header a
    # browser sends for a page opened directly from disk (file://),
    # which is exactly how this project's own single-file frontend is
    # normally opened (double-click, no local server). Without it,
    # every fetch() from that file:// page was silently blocked by
    # the browser as a CORS failure ("Failed to fetch") — a real,
    # user-reported bug (2026-09-23): the app couldn't reach the API
    # at all, not even to submit a password reset, which is why
    # resetting the password didn't fix anything either.
    allow_origins=[FRONTEND_ORIGIN, "http://127.0.0.1:5500", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------
# Security response headers (2026-09) — a standard baseline this API
# had none of before now. Applied to every response via one
# middleware rather than per-route, so nothing has to remember to set
# these. Skips CSP specifically on FastAPI's own docs routes
# (/docs, /redoc, /openapi.json) — Swagger UI/ReDoc load their JS/CSS
# from a CDN and render real HTML, and a strict CSP would break that
# UI outright; every other route in this API returns JSON only, where
# a strict `default-src 'none'` has no real cost.
# ---------------------------------------------------------------
_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Browsers ignore this entirely over plain HTTP (local dev) — sent
    # unconditionally anyway so a real HTTPS deployment gets it for
    # free without a separate prod-only code path to remember.
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path not in _DOCS_PATHS:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response

scheduler = AsyncIOScheduler()

# ---------------------------------------------------------------
# Ingestion source registry. This is the real proof the
# generalization from Step 1 actually works: UK Find a Tender is a
# genuinely different kind of source (no API key at all, no
# server-side category filter, no NAICS-style rotation needed) and
# adding it required exactly one more entry here — no scheduler
# change, no new status endpoint, no duplicated error handling.
# ---------------------------------------------------------------
INGESTION_SOURCES: list[IngestionSourceConfig] = [
    IngestionSourceConfig(
        code="sam_gov",
        display_name="SAM.gov Contract Opportunities API",
        interval_hours=SCHEDULED_INGESTION_INTERVAL_HOURS,
        api_key_env_var="SAM_GOV_API_KEY",
        run_fn=run_sam_gov_ingestion,
        scheduler_job_id="sam_gov_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="uk_find_a_tender",
        display_name="UK Find a Tender Service",
        interval_hours=int(os.environ.get("UK_FT_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # genuinely no key needed — confirmed from GOV.UK's own docs
        run_fn=run_uk_ft_ingestion,
        scheduler_job_id="uk_ft_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="ted_eu",
        display_name="EU TED (Tenders Electronic Daily)",
        interval_hours=int(os.environ.get("TED_EU_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # Search API requires no key — confirmed from official TED docs
        run_fn=run_ted_eu_ingestion,
        scheduler_job_id="ted_eu_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="cppp_india",
        display_name="CPPP (Central Public Procurement Portal, India)",
        interval_hours=int(os.environ.get("CPPP_INDIA_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # no key exists — the listing is public HTML, see cppp_india_ingestion
        run_fn=run_cppp_india_ingestion,
        scheduler_job_id="cppp_india_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="canada_buys",
        display_name="CanadaBuys (Government of Canada)",
        interval_hours=int(os.environ.get("CANADA_BUYS_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # open dataset under the Open Government Licence — no key exists
        run_fn=run_canada_buys_ingestion,
        scheduler_job_id="canada_buys_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="south_africa",
        display_name="eTenders South Africa (National Treasury)",
        interval_hours=int(os.environ.get("SOUTH_AFRICA_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # genuine open government API — no key required, verified live
        run_fn=run_south_africa_ingestion,
        scheduler_job_id="south_africa_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="colombia",
        display_name="SECOP II (Colombia Compra Eficiente)",
        interval_hours=int(os.environ.get("COLOMBIA_INGESTION_INTERVAL_HOURS", "24")),
        # SOCRATA_APP_TOKEN is read by the ingestion module but is
        # deliberately NOT declared here: an app token only raises the
        # rate limit, and declaring it would make the scheduler skip
        # this source entirely when it is absent (that is what
        # api_key_env_var means — see run_scheduled_source). Colombia
        # works with no credentials at all.
        api_key_env_var=None,
        run_fn=run_colombia_ingestion,
        scheduler_job_id="colombia_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="prozorro",
        display_name="ProZorro (Ukraine Public Procurement)",
        # Deliberately more frequent than the 24h default: each run
        # only ever sees the most recent slice of a very high-volume
        # national feed (see app/prozorro_ingestion.py), so running
        # more often narrows the gap between runs rather than trying
        # to widen what any single run covers.
        interval_hours=int(os.environ.get("PROZORRO_INGESTION_INTERVAL_HOURS", "6")),
        api_key_env_var=None,
        run_fn=run_prozorro_ingestion,
        scheduler_job_id="prozorro_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="australia",
        display_name="AusTender (Australian Government)",
        interval_hours=int(os.environ.get("AUSTRALIA_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # genuine open government API — no key required, verified live
        run_fn=run_australia_ingestion,
        scheduler_job_id="australia_scheduled_ingestion",
    ),
    IngestionSourceConfig(
        code="paraguay",
        display_name="DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)",
        interval_hours=int(os.environ.get("PARAGUAY_INGESTION_INTERVAL_HOURS", "24")),
        api_key_env_var=None,  # no registration required for read access — verified live, see db/migrations/039
        run_fn=run_paraguay_ingestion,
        scheduler_job_id="paraguay_scheduled_ingestion",
    ),
]


async def _reap_orphaned_ingestion_jobs():
    # A job's own try/except only marks it 'failed' when the RUNNING
    # PYTHON CODE gets a chance to handle an error — a process
    # restart/crash (container restart, OOM kill, host reboot) severs
    # the connection mid-run without ever reaching that except block,
    # leaving the row 'running' forever. Found live: 10 rows stuck for
    # over a day, all traced to this project's own repeated
    # `docker compose restart api` during development, not a new
    # ingestion bug. Since this runs at STARTUP, before any new job of
    # this process can exist, any row still 'running' at this point is
    # provably orphaned from a previous process — never a job this
    # process is about to continue, since nothing resumes a job by ID.
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                update ingestion_jobs
                set status = 'failed', finished_at = now(),
                    error = 'orphaned — API process restarted mid-run'
                where status = 'running'
                returning id
            """)
        )
        reaped = result.rowcount
        await session.commit()
        if reaped:
            print(f"[startup] reaped {reaped} orphaned ingestion job(s) stuck in 'running'")


async def _run_scheduled_backup():
    # Same quiet-skip-on-missing-config shape as
    # run_scheduled_source (app/ingestion_common.py) — backup isn't
    # configured until R2 credentials + BACKUP_ENCRYPTION_KEY are set
    # in .env (see app/backup.py's own docstring), and a scheduler job
    # that crashes hourly with a traceback over a genuinely-not-yet-
    # configured feature is just log noise, not a useful signal.
    async with SessionLocal() as session:
        try:
            result = await run_backup(session, triggered_by="scheduler")
            print(f"[scheduled backup] {result}")
        except BackupConfigError as e:
            print(f"[scheduled backup] skipped: {e}")
        except Exception as e:
            print(f"[scheduled backup] failed: {e}")


async def _run_scheduled_restore_drill():
    async with SessionLocal() as session:
        try:
            result = await run_restore_drill(session, triggered_by="scheduler")
            print(f"[scheduled restore drill] {result}")
        except BackupConfigError as e:
            print(f"[scheduled restore drill] skipped: {e}")
        except Exception as e:
            print(f"[scheduled restore drill] failed: {e}")


async def _run_scheduled_staleness_check():
    async with SessionLocal() as session:
        try:
            await check_staleness_and_alert(session)
        except Exception as e:
            # This check's own job dying silently would be exactly
            # the kind of gap it exists to catch in everything else —
            # logged loudly on purpose, not swallowed.
            print(f"[scheduled staleness check] failed: {e}")


async def _run_scheduled_retention_purge():
    # No config-gate like backup's own quiet-skip (app.retention has
    # no external credentials to be missing) — this always runs.
    async with SessionLocal() as session:
        try:
            result = await run_retention_purge(session, triggered_by="scheduler")
            print(f"[scheduled retention purge] {result}")
        except Exception as e:
            print(f"[scheduled retention purge] failed: {e}")


@app.on_event("startup")
async def start_scheduler():
    await _reap_orphaned_ingestion_jobs()
    for source in INGESTION_SOURCES:
        scheduler.add_job(
            run_scheduled_source,
            "interval",
            hours=source.interval_hours,
            id=source.scheduler_job_id,
            replace_existing=True,
            kwargs={"source_config": source, "session_factory": SessionLocal},
        )
    # cron, not interval — a fixed time of day (03:00 UTC, real
    # traffic for this platform's sources is lowest then) rather than
    # "every 24h from whenever the process last started", so a
    # restart never silently shifts when backups run.
    scheduler.add_job(
        _run_scheduled_backup,
        "cron",
        hour=3,
        minute=0,
        id="scheduled_backup",
        replace_existing=True,
    )
    # Weekly, not daily — a restore drill does a real pg_restore into
    # a scratch database (real I/O and CPU, minutes not seconds), so
    # daily would be needless load for a check whose whole point is
    # "is this STILL true", which doesn't change hour to hour. Sunday
    # 04:00 UTC — after both the pg_dump (03:00) and wal-g base
    # backup (03:30, db/wal-g-base-backup.cron) jobs have had time to
    # finish, so it's always verifying a fresh backup, not a stale one.
    scheduler.add_job(
        _run_scheduled_restore_drill,
        "cron",
        day_of_week="sun",
        hour=4,
        minute=0,
        id="scheduled_restore_drill",
        replace_existing=True,
    )
    # Daily, independent of the jobs above — the only way to catch
    # "the scheduler itself died and nothing ran at all" rather than
    # "a job ran and failed" (already alerted on inside run_backup/
    # run_restore_drill's own except blocks).
    scheduler.add_job(
        _run_scheduled_staleness_check,
        "cron",
        hour=6,
        minute=0,
        id="scheduled_dr_staleness_check",
        replace_existing=True,
    )
    # 03:30 UTC — after the nightly pg_dump (03:00) has already run,
    # so an old audit_log/token row is never purged before that same
    # data had its chance to be captured in that night's backup.
    scheduler.add_job(
        _run_scheduled_retention_purge,
        "cron",
        hour=3,
        minute=30,
        id="scheduled_retention_purge",
        replace_existing=True,
    )
    scheduler.start()


@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown(wait=False)


# ---------------------------------------------------------------
# Auth
# ---------------------------------------------------------------
class TokenPayload(BaseModel):
    sub: str          # user id
    tenant_id: str
    role: str
    exp: datetime
    # Session revocation (2026-09) — see migration 047's own header.
    # Compared against users.session_version on every request in
    # get_current_user below; bumping that column invalidates every
    # already-issued token for that user instantly, regardless of how
    # much of its 30-minute expiry was left. Defaults to 1 so tokens
    # issued before this field existed (none should still be alive by
    # the time this ships, given the 30-minute expiry, but defensively
    # correct either way) decode without a validation error.
    sv: int = 1


def create_access_token(user_id: str, tenant_id: str, role: str, session_version: int) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "sv": session_version,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str) -> TokenPayload:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return TokenPayload(**data)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


# Deliberately short-lived and carries a "purpose" claim TokenPayload
# doesn't have — this must never be usable as a real access token
# even if a caller tried to send it to an authenticated route
# directly, since it's issued BEFORE the second factor is checked.
MFA_CHALLENGE_MINUTES = 5


def create_mfa_challenge_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "purpose": "mfa_challenge",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=MFA_CHALLENGE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_mfa_challenge_token(token: str) -> dict:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA challenge expired or invalid — please log in again")
    if data.get("purpose") != "mfa_challenge":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA challenge token")
    return data


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenPayload:
    payload = decode_token(creds.credentials)
    # Session revocation check — one extra indexed lookup per
    # authenticated request, on every route, since get_current_user is
    # the one common dependency every protected route (directly or via
    # get_tenant_session/require_role/require_platform_admin) already
    # builds on. A partial fix (only checking on SOME routes) would
    # not actually revoke anything, since a still-valid token would
    # keep working everywhere else — same reasoning
    # require_platform_admin already applies for its own is_platform_admin
    # check, which already does an equivalent per-request DB read.
    async with SessionLocal() as session:
        result = await session.execute(
            text("select session_version from users where id = :id"), {"id": payload.sub}
        )
        row = result.first()
    if row is None or row.session_version != payload.sv:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session has been revoked — please sign in again")
    return payload


async def get_tenant_session(user: TokenPayload = Depends(get_current_user)):
    async with SessionLocal() as session:
        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"),
            {"tid": user.tenant_id},
        )
        try:
            yield session
        finally:
            await session.close()


def require_role(*allowed_roles: str):
    async def checker(user: TokenPayload = Depends(get_current_user)):
        if user.role not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this action")
        return user
    return checker


async def require_platform_admin(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    """
    Deliberately separate from require_role('admin'). That role is
    tenant-scoped — every tenant's admin is just the top user at
    their own company, with no relationship to any other tenant.
    This checks a genuinely different flag, looked up fresh from the
    database on every call (not trusted from the JWT) so a
    platform-admin grant or revocation takes effect immediately
    rather than waiting for token expiry. Nothing in signup can set
    this flag — the only way to become a platform admin is a manual
    database update run by whoever actually operates this platform.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            text("select is_platform_admin from users where id = :uid"), {"uid": user.sub}
        )
        row = result.first()
    if row is None or not row.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform admin access required")
    return user


async def write_audit_log(
    session: AsyncSession,
    tenant_id: str,
    user_id: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[str],
    before: Optional[dict],
    after: Optional[dict],
):
    # asyncpg does not auto-serialize a Python dict into a jsonb
    # column when the SQL is issued as raw text() — it needs an
    # actual JSON string, explicitly cast to jsonb.
    await session.execute(
        text("""
            insert into audit_log
                (tenant_id, user_id, action, entity_type, entity_id, before_state, after_state)
            values
                (:tenant_id, :user_id, :action, :entity_type, :entity_id,
                 CAST(:before AS jsonb), CAST(:after AS jsonb))
        """),
        {
            "tenant_id": tenant_id, "user_id": user_id, "action": action,
            "entity_type": entity_type, "entity_id": entity_id,
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
        },
    )


# ---------------------------------------------------------------
# Signup / Login — the piece that was missing. Signup creates a
# tenant AND its first user (an admin) in one call, since there's
# no such thing as a user without a tenant in this model.
# ---------------------------------------------------------------
class SignupIn(BaseModel):
    company_name: str
    full_name: str
    email: EmailStr
    password: str
    # Everything below is optional — required-field creep is exactly
    # what makes a real prospect abandon a signup form, and none of
    # these block the account from working. They exist to pre-fill
    # the Contact tab later (see GET /auth/me and renderContact in
    # the frontend), not to gate signup on them being present.
    title: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginOut(BaseModel):
    """
    /auth/login's real response shape, once MFA exists: either a
    real access_token (no MFA enabled) OR mfa_required=true plus a
    short-lived mfa_challenge_token to hand to /auth/login/mfa —
    never both. access_token stays Optional here specifically so an
    MFA-gated login cannot accidentally carry a usable token before
    the second factor is checked.
    """
    access_token: Optional[str] = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_challenge_token: Optional[str] = None


@app.post("/auth/signup", response_model=TokenOut, status_code=201)
@limiter.limit(SIGNUP_RATE_LIMIT)
async def signup(request: Request, body: SignupIn):
    if len(body.password) < 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password must be at least 10 characters")

    password_hash = ph.hash(body.password)

    async with SessionLocal() as session:
        existing = await session.execute(
            text("select id from users where email = :email"), {"email": body.email}
        )
        if existing.first():
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

        tenant_result = await session.execute(
            text("""
                insert into tenants (name, website, country, phone, share_token)
                values (:name, :website, :country, :phone, encode(gen_random_bytes(16), 'hex'))
                returning id
            """),
            {
                "name": body.company_name, "website": body.website,
                "country": body.country, "phone": body.phone,
            },
        )
        tenant_id = tenant_result.scalar_one()

        admin_role = await session.execute(text("select id from roles where name = 'admin'"))
        admin_role_id = admin_role.scalar_one()

        user_result = await session.execute(
            text("""
                insert into users (tenant_id, email, password_hash, role_id, full_name, title, phone)
                values (:tenant_id, :email, :password_hash, :role_id, :full_name, :title, :phone)
                returning id
            """),
            {
                "tenant_id": str(tenant_id), "email": body.email,
                "password_hash": password_hash, "role_id": str(admin_role_id),
                "full_name": body.full_name, "title": body.title, "phone": body.phone,
            },
        )
        user_id = user_result.scalar_one()

        await session.execute(
            text("update users set email_verified = false where id = :id"),
            {"id": str(user_id)},
        )

        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        await write_audit_log(
            session, str(tenant_id), str(user_id), "tenant.created_via_signup",
            "tenant", str(tenant_id), None, {"company_name": body.company_name},
        )

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=EMAIL_VERIFICATION_TOKEN_MINUTES)
        await session.execute(
            text("""
                insert into email_verification_tokens (user_id, token_hash, expires_at)
                values (:user_id, :token_hash, :expires_at)
            """),
            {"user_id": str(user_id), "token_hash": _hash_token(raw_token), "expires_at": expires_at},
        )
        await session.commit()

    verify_link = f"{FRONTEND_ORIGIN}/defence-opportunity-intelligence-app-v12.html?verify_email_token={raw_token}"
    send_email(
        body.email,
        "Verify your Defence Opportunity Intelligence email",
        f"Welcome! Please confirm this is your email address to finish setting up your account.\n\n"
        f"Verify your email here (expires in {EMAIL_VERIFICATION_TOKEN_MINUTES // 60} hours):\n{verify_link}\n\n"
        f"You can still sign in and use the app before verifying — this just confirms we can reach you.",
    )

    token = create_access_token(str(user_id), str(tenant_id), "admin", session_version=1)
    return TokenOut(access_token=token)


@app.post("/auth/login", response_model=LoginOut)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(request: Request, body: LoginIn):
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                select u.id, u.tenant_id, u.password_hash, r.name as role_name, u.mfa_enabled,
                       u.failed_login_attempts, u.locked_until, u.session_version
                from users u join roles r on r.id = u.role_id
                where u.email = :email and u.is_active = true
            """),
            {"email": body.email},
        )
        row = result.first()

        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

        # Locked takes priority over checking the password at all —
        # a correct password during a lockout window must not reset
        # anything or leak "actually that password was right," since
        # the whole point of a lockout is that a real credential leak
        # doesn't help an attacker until the window passes.
        if row.locked_until and row.locked_until > datetime.now(timezone.utc):
            minutes_left = max(1, int((row.locked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
            raise HTTPException(
                status.HTTP_423_LOCKED,
                f"Too many failed sign-in attempts. Try again in {minutes_left} minute{'s' if minutes_left != 1 else ''}.",
            )

        try:
            ph.verify(row.password_hash, body.password)
        except VerifyMismatchError:
            new_attempts = row.failed_login_attempts + 1
            now_locked = new_attempts >= MAX_FAILED_LOGIN_ATTEMPTS
            await session.execute(
                text("""
                    update users
                    set failed_login_attempts = :attempts,
                        locked_until = :locked_until
                    where id = :id
                """),
                {
                    "attempts": 0 if now_locked else new_attempts,
                    "locked_until": (datetime.now(timezone.utc) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)) if now_locked else None,
                    "id": str(row.id),
                },
            )
            await session.commit()
            if now_locked:
                raise HTTPException(
                    status.HTTP_423_LOCKED,
                    f"Too many failed sign-in attempts. Try again in {LOGIN_LOCKOUT_MINUTES} minutes.",
                )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

        # A correct password clears any stale count/lock — a genuine
        # login shouldn't stay one attempt away from locking out just
        # because of a few earlier mistyped passwords.
        if row.failed_login_attempts or row.locked_until:
            await session.execute(
                text("update users set failed_login_attempts = 0, locked_until = null where id = :id"),
                {"id": str(row.id)},
            )
            await session.commit()

    if row.mfa_enabled:
        # Password alone is correct but not sufficient — issue only a
        # short-lived challenge token, never the real access_token,
        # until /auth/login/mfa confirms the second factor too.
        challenge_token = create_mfa_challenge_token(str(row.id), str(row.tenant_id), row.role_name)
        return LoginOut(mfa_required=True, mfa_challenge_token=challenge_token)

    token = create_access_token(str(row.id), str(row.tenant_id), row.role_name, session_version=row.session_version)
    return LoginOut(access_token=token)


class MfaLoginIn(BaseModel):
    mfa_challenge_token: str
    code: str


@app.post("/auth/login/mfa", response_model=TokenOut)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login_mfa(request: Request, body: MfaLoginIn):
    """
    The second step of an MFA-gated login. Accepts EITHER a real
    6-digit TOTP code OR one of the user's remaining backup codes —
    a used backup code is removed from the stored array so it can
    never be replayed. Rate-limited the same as /auth/login itself:
    a 6-digit TOTP code is only 1,000,000 possibilities, but that
    limiter (not the code's own entropy) is what actually makes
    guessing impractical here, same as any password gate.
    """
    claims = decode_mfa_challenge_token(body.mfa_challenge_token)
    user_id = claims["sub"]

    async with SessionLocal() as session:
        result = await session.execute(
            text("select mfa_secret, mfa_backup_codes, session_version from users where id = :id and mfa_enabled = true"),
            {"id": user_id},
        )
        row = result.first()
        if row is None or not row.mfa_secret:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA is not enabled for this account")

        try:
            secret = decrypt_secret(row.mfa_secret)
        except MfaConfigError:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "MFA verification is temporarily unavailable")

        if verify_totp(secret, body.code):
            pass  # real TOTP code, nothing else to do
        elif verify_backup_code(body.code, row.mfa_backup_codes or []):
            used_hash = hash_backup_code(body.code)
            remaining = [h for h in (row.mfa_backup_codes or []) if h != used_hash]
            await session.execute(
                text("update users set mfa_backup_codes = :codes where id = :id"),
                {"codes": remaining, "id": user_id},
            )
            await session.execute(
                text("select set_config('app.current_tenant', :tid, true)"),
                {"tid": claims["tenant_id"]},
            )
            await write_audit_log(
                session, claims["tenant_id"], user_id, "user.mfa_backup_code_used",
                "user", user_id, None, {"codes_remaining": len(remaining)},
            )
            await session.commit()
        else:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication code")

    token = create_access_token(user_id, claims["tenant_id"], claims["role"], session_version=row.session_version)
    return TokenOut(access_token=token)


# ---------------------------------------------------------------
# Forgot / Reset Password. Two routes, deliberately shaped so
# neither one ever reveals whether a given email address has an
# account:
#   - forgot-password ALWAYS returns the same generic message,
#     whether or not the email matched a user, and takes roughly the
#     same time either way (no early-return on "not found" before
#     the token-generation/email-send work) — a different response
#     or a faster reply for a nonexistent email is exactly the kind
#     of oracle an account-enumeration attack uses.
#   - reset-password's token is a random 32-byte value, sent to the
#     user only in the email itself; the DB stores just its SHA-256,
#     the same "hash, never the secret" discipline as password_hash.
# ---------------------------------------------------------------
PASSWORD_RESET_TOKEN_MINUTES = 60
EMAIL_VERIFICATION_TOKEN_MINUTES = 24 * 60


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


def _hash_token(raw_token: str) -> str:
    """
    Generic "hash a random one-time token" helper — was named
    `_hash_reset_token` until email verification (2026-09) needed the
    exact same primitive for a different table
    (`email_verification_tokens`, not `password_reset_tokens`).
    Renamed rather than duplicated; the two call sites below are
    updated to match.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


@app.post("/auth/forgot-password")
@limiter.limit(PASSWORD_RESET_RATE_LIMIT)
async def forgot_password(request: Request, body: ForgotPasswordIn):
    generic_response = {
        "message": "If an account exists for that email, a password reset link has been sent."
    }

    async with SessionLocal() as session:
        result = await session.execute(
            text("select id from users where email = :email and is_active = true"),
            {"email": body.email},
        )
        row = result.first()
        if row is None:
            # Deliberately no early return before this point in the
            # function that would make a nonexistent email visibly
            # faster — see the module note above.
            return generic_response

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_MINUTES)
        await session.execute(
            text("""
                insert into password_reset_tokens (user_id, token_hash, expires_at)
                values (:user_id, :token_hash, :expires_at)
            """),
            {"user_id": str(row.id), "token_hash": _hash_token(raw_token), "expires_at": expires_at},
        )
        await session.commit()

    reset_link = f"{FRONTEND_ORIGIN}/defence-opportunity-intelligence-app-v12.html?reset_token={raw_token}"
    send_email(
        body.email,
        "Reset your Defence Opportunity Intelligence password",
        f"Someone requested a password reset for this account.\n\n"
        f"Reset your password here (expires in {PASSWORD_RESET_TOKEN_MINUTES} minutes):\n{reset_link}\n\n"
        f"If you didn't request this, you can ignore this email — your password hasn't changed.",
    )
    return generic_response


@app.post("/auth/reset-password")
@limiter.limit(PASSWORD_RESET_RATE_LIMIT)
async def reset_password(request: Request, body: ResetPasswordIn):
    if len(body.new_password) < 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password must be at least 10 characters")

    token_hash = _hash_token(body.token)
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                select id, user_id from password_reset_tokens
                where token_hash = :token_hash
                  and used_at is null
                  and expires_at > now()
            """),
            {"token_hash": token_hash},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired — request a new one")

        new_hash = ph.hash(body.new_password)
        # session_version + 1 (2026-09, migration 047) — a reset link
        # exists to recover a locked-out or compromised account, so
        # whatever session let an attacker in (if that's why this
        # link was needed) must not keep working just because its own
        # 30-minute expiry hadn't run out yet.
        await session.execute(
            text("""
                update users
                set password_hash = :hash, session_version = session_version + 1
                where id = :user_id
            """),
            {"hash": new_hash, "user_id": str(row.user_id)},
        )
        await session.execute(
            text("update password_reset_tokens set used_at = now() where id = :id"),
            {"id": str(row.id)},
        )
        await session.commit()

    return {"message": "Password updated — you can now sign in with your new password."}


# ---------------------------------------------------------------
# Email verification. Unlike password reset, an unverified account
# can still sign in and use the app (see signup's comment) — this
# just confirms the signer-upper controls the inbox they typed, so it
# has no "always the same generic response" requirement the way
# forgot-password does (there is no account-enumeration risk here:
# the caller already has a real access_token/is-signed-in when they
# hit /auth/verify-email or resend, since both take the email off the
# authenticated user rather than an arbitrary body field).
# ---------------------------------------------------------------
class VerifyEmailIn(BaseModel):
    token: str


@app.post("/auth/verify-email")
@limiter.limit(PASSWORD_RESET_RATE_LIMIT)
async def verify_email(request: Request, body: VerifyEmailIn):
    token_hash = _hash_token(body.token)
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                select id, user_id from email_verification_tokens
                where token_hash = :token_hash
                  and used_at is null
                  and expires_at > now()
            """),
            {"token_hash": token_hash},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This verification link is invalid or has expired — request a new one")

        await session.execute(
            text("update users set email_verified = true where id = :id"),
            {"id": str(row.user_id)},
        )
        await session.execute(
            text("update email_verification_tokens set used_at = now() where id = :id"),
            {"id": str(row.id)},
        )
        await session.commit()

    return {"message": "Email verified."}


@app.post("/auth/resend-verification")
@limiter.limit(PASSWORD_RESET_RATE_LIMIT)
async def resend_verification(request: Request, user: TokenPayload = Depends(get_current_user)):
    async with SessionLocal() as session:
        result = await session.execute(
            text("select email, email_verified from users where id = :id"),
            {"id": user.sub},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if row.email_verified:
            return {"message": "This email is already verified."}

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=EMAIL_VERIFICATION_TOKEN_MINUTES)
        await session.execute(
            text("""
                insert into email_verification_tokens (user_id, token_hash, expires_at)
                values (:user_id, :token_hash, :expires_at)
            """),
            {"user_id": user.sub, "token_hash": _hash_token(raw_token), "expires_at": expires_at},
        )
        await session.commit()

    verify_link = f"{FRONTEND_ORIGIN}/defence-opportunity-intelligence-app-v12.html?verify_email_token={raw_token}"
    send_email(
        row.email,
        "Verify your Defence Opportunity Intelligence email",
        f"Verify your email here (expires in {EMAIL_VERIFICATION_TOKEN_MINUTES // 60} hours):\n{verify_link}\n\n"
        f"You can still sign in and use the app before verifying — this just confirms we can reach you.",
    )
    return {"message": "Verification email sent."}


# ---------------------------------------------------------------
# SSO via OIDC (2026-09) — "Sign in with Google/Microsoft/any OIDC
# provider", additive to email+password, never replacing it. Full
# design rationale (PKCE, signed-state instead of server sessions,
# why (provider, subject) not email is the real identity) lives in
# app/oidc.py's own module docstring — this is thin orchestration
# only, the same split every other feature in this file follows.
#
# Three-step flow, mirroring the shape OAuth/OIDC always takes:
#   1. GET  /auth/oidc/{provider}/login    — redirects to the real
#      provider's own authorization endpoint.
#   2. GET  /auth/oidc/{provider}/callback — the provider redirects
#      the browser back here with a code; exchanged + verified, then
#      EITHER a known identity logs straight in, OR (first time this
#      email has ever been seen) the browser is sent to the frontend
#      with a short-lived signup token instead of a real access_token.
#   3. POST /auth/oidc/complete-signup     — only reached for a truly
#      new account: takes that signup token + a company_name (the one
#      thing an OIDC provider can never supply, this being a
#      multi-tenant B2B platform) and creates the tenant exactly like
#      /auth/signup does, then issues a real access_token.
# ---------------------------------------------------------------
OIDC_SIGNUP_TOKEN_MINUTES = 15


@app.get("/auth/oidc/{provider}/login")
@limiter.limit(LOGIN_RATE_LIMIT)
async def oidc_login(request: Request, provider: str):
    try:
        config = get_provider_config(provider)
        discovery = await discover_provider(config["issuer"])
    except OidcConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach SSO provider '{provider}': {e}")

    code_verifier, code_challenge = make_pkce_pair()
    nonce = secrets.token_urlsafe(24)
    state = make_state_token(JWT_SECRET, provider, code_verifier, nonce, redirect_after=None)
    redirect_uri = f"{API_BASE_URL}/auth/oidc/{provider}/callback"

    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{discovery['authorization_endpoint']}?{urlencode(params)}"
    return RedirectResponse(auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/auth/oidc/{provider}/callback")
@limiter.limit(LOGIN_RATE_LIMIT)
async def oidc_callback(request: Request, provider: str, code: str, state: str):
    frontend_page = f"{FRONTEND_ORIGIN}/defence-opportunity-intelligence-app-v12.html"
    try:
        state_payload = decode_state_token(JWT_SECRET, state)
        if state_payload["provider"] != provider:
            raise OidcVerificationError("SSO state does not match the callback provider")

        config = get_provider_config(provider)
        discovery = await discover_provider(config["issuer"])
        redirect_uri = f"{API_BASE_URL}/auth/oidc/{provider}/callback"

        tokens = await exchange_code_for_tokens(
            discovery["token_endpoint"], config["client_id"], config["client_secret"],
            code, redirect_uri, state_payload["code_verifier"],
        )
        id_token = tokens.get("id_token")
        if not id_token:
            raise OidcVerificationError("Provider response had no id_token")

        claims = verify_id_token(
            id_token, discovery["jwks_uri"], discovery["issuer"], config["client_id"], state_payload["nonce"],
        )
    except (OidcConfigError, OidcVerificationError) as e:
        return RedirectResponse(f"{frontend_page}?oidc_error={quote(str(e))}", status_code=status.HTTP_302_FOUND)
    except httpx.HTTPError as e:
        return RedirectResponse(f"{frontend_page}?oidc_error={quote(f'Could not reach SSO provider: {e}')}", status_code=status.HTTP_302_FOUND)

    subject = claims["sub"]
    email = claims["email"]
    name = claims.get("name") or email

    async with SessionLocal() as session:
        # 1. Already-linked identity — the returning-user path.
        linked = (await session.execute(
            text("""
                select u.id, u.tenant_id, r.name as role_name, u.session_version, u.is_active, u.mfa_enabled
                from oidc_identities oi
                join users u on u.id = oi.user_id
                join roles r on r.id = u.role_id
                where oi.provider = :provider and oi.subject = :subject
            """),
            {"provider": provider, "subject": subject},
        )).first()

        if linked is not None:
            if not linked.is_active:
                return RedirectResponse(f"{frontend_page}?oidc_error={quote('This account has been deactivated.')}", status_code=status.HTTP_302_FOUND)
            # MFA is a per-account factor, not tied to HOW a login
            # started — an SSO login that skipped straight to a real
            # access_token for an MFA-enabled account would be a
            # genuine bypass of that account's own second factor,
            # found live (2026-09) while testing against a real
            # MFA-enabled account: this must hand back the same
            # short-lived MFA challenge /auth/login already issues,
            # not a real token, and let the EXISTING
            # /auth/login/mfa + its frontend screen finish the job.
            if linked.mfa_enabled:
                challenge_token = create_mfa_challenge_token(str(linked.id), str(linked.tenant_id), linked.role_name)
                return RedirectResponse(f"{frontend_page}?oidc_mfa_challenge_token={challenge_token}", status_code=status.HTTP_302_FOUND)
            token = create_access_token(str(linked.id), str(linked.tenant_id), linked.role_name, session_version=linked.session_version)
            return RedirectResponse(f"{frontend_page}?oidc_token={token}", status_code=status.HTTP_302_FOUND)

        # 2. No linked identity yet, but an existing password account
        # uses this same PROVIDER-VERIFIED email — auto-link rather
        # than force a redundant second signup. Safe specifically
        # because verify_id_token already rejected an unverified
        # email claim above; an unverified email could never reach
        # this line.
        existing = (await session.execute(
            text("""
                select u.id, u.tenant_id, r.name as role_name, u.session_version, u.is_active, u.mfa_enabled
                from users u join roles r on r.id = u.role_id
                where lower(u.email) = lower(:email)
            """),
            {"email": email},
        )).first()

        if existing is not None:
            if not existing.is_active:
                return RedirectResponse(f"{frontend_page}?oidc_error={quote('This account has been deactivated.')}", status_code=status.HTTP_302_FOUND)
            await session.execute(
                text("insert into oidc_identities (user_id, provider, subject, email) values (:uid, :provider, :subject, :email)"),
                {"uid": str(existing.id), "provider": provider, "subject": subject, "email": email},
            )
            await session.execute(
                text("select set_config('app.current_tenant', :tid, true)"),
                {"tid": str(existing.tenant_id)},
            )
            await write_audit_log(
                session, str(existing.tenant_id), str(existing.id), "user.oidc_identity_linked",
                "user", str(existing.id), None, {"provider": provider},
            )
            await session.commit()
            # Same MFA-must-still-apply rule as the already-linked
            # path above — auto-linking proves the email, not the
            # second factor this account itself requires.
            if existing.mfa_enabled:
                challenge_token = create_mfa_challenge_token(str(existing.id), str(existing.tenant_id), existing.role_name)
                return RedirectResponse(f"{frontend_page}?oidc_mfa_challenge_token={challenge_token}", status_code=status.HTTP_302_FOUND)
            token = create_access_token(str(existing.id), str(existing.tenant_id), existing.role_name, session_version=existing.session_version)
            return RedirectResponse(f"{frontend_page}?oidc_token={token}", status_code=status.HTTP_302_FOUND)

    # 3. Genuinely new — this platform has no tenant for this person
    # yet, and only a human can supply a company_name. Hand the
    # browser a short-lived signup token instead of an access_token.
    signup_payload = {
        "provider": provider, "subject": subject, "email": email, "name": name,
        "purpose": "oidc_signup",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=OIDC_SIGNUP_TOKEN_MINUTES),
    }
    signup_token = jwt.encode(signup_payload, JWT_SECRET, algorithm=JWT_ALGO)
    return RedirectResponse(
        f"{frontend_page}?oidc_signup_token={signup_token}&oidc_email={quote(email)}&oidc_name={quote(name)}",
        status_code=status.HTTP_302_FOUND,
    )


class OidcCompleteSignupIn(BaseModel):
    signup_token: str
    company_name: str
    full_name: Optional[str] = None


@app.post("/auth/oidc/complete-signup", response_model=TokenOut, status_code=201)
@limiter.limit(SIGNUP_RATE_LIMIT)
async def oidc_complete_signup(request: Request, body: OidcCompleteSignupIn):
    try:
        payload = jwt.decode(body.signup_token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This sign-up link has expired — please sign in with SSO again")
    if payload.get("purpose") != "oidc_signup":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid sign-up token")

    provider, subject, email = payload["provider"], payload["subject"], payload["email"]
    full_name = body.full_name or payload.get("name") or email

    async with SessionLocal() as session:
        existing = await session.execute(text("select id from users where lower(email) = lower(:email)"), {"email": email})
        if existing.first():
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists — sign in with SSO instead")

        tenant_result = await session.execute(
            text("""
                insert into tenants (name, share_token)
                values (:name, encode(gen_random_bytes(16), 'hex'))
                returning id
            """),
            {"name": body.company_name},
        )
        tenant_id = tenant_result.scalar_one()

        admin_role = await session.execute(text("select id from roles where name = 'admin'"))
        admin_role_id = admin_role.scalar_one()

        # No password_hash — an OIDC-only account has nothing to
        # brute-force at /auth/login with, since that route requires
        # a matching hash. random, never-shown value rather than NULL,
        # since password_hash is NOT NULL in the schema; argon2 never
        # matches a value it didn't itself hash from a real attempt.
        user_result = await session.execute(
            text("""
                insert into users (tenant_id, email, password_hash, role_id, full_name, email_verified)
                values (:tenant_id, :email, :password_hash, :role_id, :full_name, true)
                returning id
            """),
            {
                "tenant_id": str(tenant_id), "email": email,
                "password_hash": ph.hash(secrets.token_urlsafe(32)),
                "role_id": str(admin_role_id), "full_name": full_name,
            },
        )
        user_id = user_result.scalar_one()

        await session.execute(
            text("insert into oidc_identities (user_id, provider, subject, email) values (:uid, :provider, :subject, :email)"),
            {"uid": str(user_id), "provider": provider, "subject": subject, "email": email},
        )

        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        await write_audit_log(
            session, str(tenant_id), str(user_id), "tenant.created_via_oidc_signup",
            "tenant", str(tenant_id), None, {"company_name": body.company_name, "provider": provider},
        )
        await session.commit()

    token = create_access_token(str(user_id), str(tenant_id), "admin", session_version=1)
    return TokenOut(access_token=token)


# ---------------------------------------------------------------
# Change Password (logged in) — a real, standing gap: this project's
# only way to change a password was the forgot/reset-password email
# flow, which needs a real SMTP account (dev-mode just logs the link
# to console, see app/email_sender.py). A signed-in user shouldn't
# need to sign out, "forget" a password they still remember, and wait
# on an email just to change it. Requires the CURRENT password (not
# just an active session) — the same re-auth-for-a-security-lowering-
# or-changing-action discipline as /auth/mfa/disable. Rate-limited
# (2026-09, found in a manual audit) for the same reason: re-checking
# a password with no throttle turns a stolen/hijacked session token
# into an unlimited password-guessing oracle, the one class of attack
# LOGIN_RATE_LIMIT + account lockout already exist to prevent on
# /auth/login itself.
# ---------------------------------------------------------------
class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@app.post("/auth/change-password")
@limiter.limit(LOGIN_RATE_LIMIT)
async def change_password(
    request: Request,
    body: ChangePasswordIn,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    if len(body.new_password) < 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "New password must be at least 10 characters")

    result = await session.execute(
        text("select password_hash from users where id = :id"), {"id": user.sub}
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    try:
        ph.verify(row.password_hash, body.current_password)
    except VerifyMismatchError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")

    new_hash = ph.hash(body.new_password)
    # session_version + 1 (2026-09, migration 047) — including THIS
    # request's own token: it already passed get_current_user's check
    # before this handler ran, so this request still completes, but
    # every token (this one included) stops working on the very next
    # request. The frontend forces a fresh sign-in right after a
    # successful change for exactly this reason, not just as a nicety.
    await session.execute(
        text("update users set password_hash = :hash, session_version = session_version + 1 where id = :id"),
        {"hash": new_hash, "id": user.sub},
    )
    await write_audit_log(
        session, user.tenant_id, user.sub, "user.password_changed", "user", user.sub, None, None
    )
    await session.commit()
    return {"message": "Password updated. Please sign in again.", "session_revoked": True}


# ---------------------------------------------------------------
# Session revocation (2026-09, migration 047) — self-service
# "log out everywhere." A user who suspects their session/device was
# compromised shouldn't have to wait up to 30 minutes (ACCESS_TOKEN_MINUTES)
# for a stolen token to expire on its own, or need a platform admin's
# help the way blocking/resetting-for-them does. Deliberately kills
# THIS request's own token too, same as /auth/change-password above —
# "log out everywhere" that left the caller's own current tab logged
# in wouldn't actually be everywhere.
# ---------------------------------------------------------------
@app.post("/auth/logout-everywhere")
async def logout_everywhere(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    await session.execute(
        text("update users set session_version = session_version + 1 where id = :id"),
        {"id": user.sub},
    )
    await write_audit_log(
        session, user.tenant_id, user.sub, "user.logged_out_everywhere", "user", user.sub, None, None
    )
    await session.commit()
    return {"message": "You've been signed out of every device and session."}


# ---------------------------------------------------------------
# Platform Admin — list users across EVERY tenant, so the password-
# reset action below can be a "search, select, click" flow rather
# than requiring the admin to already know a user's exact email by
# heart. Excludes pytest's own throwaway tenants (named "Test Company
# <hex>" by tests/conftest.py's new_tenant fixture — see the 2026-09
# cleanup in this file's own history that removed 8155 of them) from
# the default listing, since this dev database still accumulates
# more every time the test suite runs and they'd otherwise drown out
# every real account; an explicit search still reaches them if truly
# needed (e.g. debugging a specific test run), since a real support
# question is never "reset a fixture's password."
# ---------------------------------------------------------------
@app.get("/platform-admin/users")
async def list_all_users(
    search: Optional[str] = None,
    limit: int = 50,
    admin: TokenPayload = Depends(require_platform_admin),
):
    limit = max(1, min(limit, 200))
    async with SessionLocal() as session:
        conditions = []
        params: dict = {"limit": limit}
        if search:
            conditions.append("(u.email ilike :search or t.name ilike :search or u.full_name ilike :search)")
            params["search"] = f"%{search}%"
        else:
            conditions.append("t.name not like 'Test Company%'")
        where_sql = " and ".join(conditions)

        result = await session.execute(
            text(f"""
                select u.id, u.email, u.full_name, u.is_active, u.mfa_enabled, u.created_at,
                       u.locked_until,
                       t.id as tenant_id, t.name as tenant_name, r.name as role_name
                from users u
                join tenants t on t.id = u.tenant_id
                join roles r on r.id = u.role_id
                where {where_sql}
                order by u.created_at desc
                limit :limit
            """),
            params,
        )
        users = [dict(r._mapping) for r in result]
    return {"returned": len(users), "users": users}


# ---------------------------------------------------------------
# Platform Admin — block/unblock ANY tenant's user. Reuses
# `users.is_active`, a column that already existed and was already
# enforced at login (`/auth/login`'s own query has always filtered on
# `is_active = true`) — there was previously just no route to ever
# flip it. The real use case that prompted this: a tenant's paid
# service period ending should be able to suspend their access
# without deleting their account or its data, the same distinction
# every real SaaS makes between "cancelled" and "gone." Also bumps
# session_version (2026-09, migration 047) — a corrected note: this
# used to say a blocked user's existing JWT kept working until its
# own 30-minute expiry, an accepted gap at the time. Session
# revocation closes it: blocking now takes effect on this user's very
# NEXT request, not just their next login.
# ---------------------------------------------------------------
@app.post("/platform-admin/users/{user_id}/block")
async def platform_admin_block_user(
    user_id: str,
    admin: TokenPayload = Depends(require_platform_admin),
):
    if user_id == admin.sub:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot block your own account")

    async with SessionLocal() as session:
        result = await session.execute(
            text("select id, tenant_id, email, is_active from users where id = :id"), {"id": user_id}
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if not row.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "This account is already blocked")

        await session.execute(
            text("update users set is_active = false, session_version = session_version + 1 where id = :id"),
            {"id": user_id},
        )
        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"), {"tid": str(row.tenant_id)}
        )
        await write_audit_log(
            session, str(row.tenant_id), admin.sub, "platform_admin.user_blocked",
            "user", user_id, None, {"email": row.email},
        )
        await session.commit()
    return {"message": f"{row.email} has been blocked and can no longer sign in."}


@app.post("/platform-admin/users/{user_id}/unblock")
async def platform_admin_unblock_user(
    user_id: str,
    admin: TokenPayload = Depends(require_platform_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            text("select id, tenant_id, email, is_active from users where id = :id"), {"id": user_id}
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if row.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "This account is not blocked")

        await session.execute(
            text("update users set is_active = true where id = :id"), {"id": user_id}
        )
        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"), {"tid": str(row.tenant_id)}
        )
        await write_audit_log(
            session, str(row.tenant_id), admin.sub, "platform_admin.user_unblocked",
            "user", user_id, None, {"email": row.email},
        )
        await session.commit()
    return {"message": f"{row.email} has been unblocked and can sign in again."}


# ---------------------------------------------------------------
# Platform Admin — clear a login lockout early. Deliberately a
# SEPARATE action/route from block/unblock above, not folded into it
# — blocking is a deliberate, indefinite admin decision (a service
# period ending); a lockout is the login route's own temporary,
# self-clearing state after too many wrong passwords, and the two
# must never be confused with each other in the audit log or the UI.
# ---------------------------------------------------------------
@app.post("/platform-admin/users/{user_id}/unlock")
async def platform_admin_unlock_user(
    user_id: str,
    admin: TokenPayload = Depends(require_platform_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            text("select id, tenant_id, email, locked_until from users where id = :id"), {"id": user_id}
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if not row.locked_until:
            raise HTTPException(status.HTTP_409_CONFLICT, "This account is not locked")

        await session.execute(
            text("update users set failed_login_attempts = 0, locked_until = null where id = :id"),
            {"id": user_id},
        )
        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"), {"tid": str(row.tenant_id)}
        )
        await write_audit_log(
            session, str(row.tenant_id), admin.sub, "platform_admin.user_unlocked",
            "user", user_id, None, {"email": row.email},
        )
        await session.commit()
    return {"message": f"{row.email}'s login lockout has been cleared."}


# ---------------------------------------------------------------
# Platform Admin — one user's own activity log. `audit_log` has been
# written to since Phase 0 (23 call sites across this file: signups,
# password/MFA changes, product/opportunity actions, taxonomy edits,
# every platform-admin action on this page itself) but never had
# anywhere to actually be READ from — this is that. RLS on audit_log
# requires app.current_tenant to match, so the target user's own
# tenant_id is looked up first and set before querying, the same
# pattern every other platform-admin route already uses to act
# outside its own caller's tenant.
# ---------------------------------------------------------------
@app.get("/platform-admin/users/{user_id}/activity")
async def platform_admin_user_activity(
    user_id: str,
    limit: int = 100,
    admin: TokenPayload = Depends(require_platform_admin),
):
    limit = max(1, min(limit, 500))
    async with SessionLocal() as session:
        user_result = await session.execute(
            text("select id, tenant_id, email from users where id = :id"), {"id": user_id}
        )
        user_row = user_result.first()
        if user_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"), {"tid": str(user_row.tenant_id)}
        )
        result = await session.execute(
            text("""
                select action, entity_type, entity_id, before_state, after_state, created_at
                from audit_log
                where user_id = :user_id
                order by created_at desc
                limit :limit
            """),
            {"user_id": user_id, "limit": limit},
        )
        entries = [dict(r._mapping) for r in result]
    return {"email": user_row.email, "returned": len(entries), "entries": entries}


# ---------------------------------------------------------------
# Platform Admin — trigger a password reset for ANY tenant's user.
# The real, deliberate design choice here: this NEVER lets a platform
# admin see or set a user's actual new password — it only generates
# the exact same one-time reset link /auth/forgot-password already
# does, reusing the same password_reset_tokens mechanism, so the
# target user still proves it's them and still picks their OWN final
# password. A route that let an admin set an arbitrary password
# directly would mean the operator now KNOWS a customer's password
# (a real, separate risk — password reuse across sites is common),
# and would be a much easier support-abuse vector to get wrong. The
# raw link is returned directly in the response (unlike the public
# forgot-password route, which must stay silent about whether an
# email exists to avoid account enumeration) — the caller here is
# already an authenticated platform admin asking about one specific,
# known email, so there's no enumeration risk left to guard against.
# ---------------------------------------------------------------
class PlatformAdminResetPasswordIn(BaseModel):
    email: EmailStr


@app.post("/platform-admin/users/reset-password")
async def platform_admin_trigger_password_reset(
    body: PlatformAdminResetPasswordIn,
    admin: TokenPayload = Depends(require_platform_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            text("select id, tenant_id from users where email = :email and is_active = true"),
            {"email": body.email},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No active account with that email")

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_MINUTES)
        await session.execute(
            text("""
                insert into password_reset_tokens (user_id, token_hash, expires_at)
                values (:user_id, :token_hash, :expires_at)
            """),
            {"user_id": str(row.id), "token_hash": _hash_token(raw_token), "expires_at": expires_at},
        )
        # session_version + 1 (2026-09, migration 047) — an admin
        # triggering this is usually because the account may be
        # compromised or locked out; kill any existing sessions at the
        # same time as generating the recovery link, not just once the
        # new password is actually set.
        await session.execute(
            text("update users set session_version = session_version + 1 where id = :id"),
            {"id": str(row.id)},
        )
        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"),
            {"tid": str(row.tenant_id)},
        )
        await write_audit_log(
            session, str(row.tenant_id), admin.sub, "platform_admin.password_reset_triggered",
            "user", str(row.id), None, {"triggered_for_email": body.email},
        )
        await session.commit()

    reset_link = f"{FRONTEND_ORIGIN}/defence-opportunity-intelligence-app-v12.html?reset_token={raw_token}"
    send_email(
        body.email,
        "Reset your Defence Opportunity Intelligence password",
        f"A platform administrator triggered a password reset for this account.\n\n"
        f"Reset your password here (expires in {PASSWORD_RESET_TOKEN_MINUTES} minutes):\n{reset_link}\n\n"
        f"If you did not expect this, contact support immediately.",
    )
    return {"reset_link": reset_link, "expires_in_minutes": PASSWORD_RESET_TOKEN_MINUTES}


# ---------------------------------------------------------------
# MFA (TOTP) enrollment — three steps, deliberately never two:
#   1. /auth/mfa/setup   — generates + stores an ENCRYPTED secret,
#      but mfa_enabled stays false. A secret the user hasn't proven
#      they can actually use yet must never gate their own login.
#   2. /auth/mfa/enable  — the user proves the secret works by
#      submitting one real code from their authenticator app; only
#      THEN does mfa_enabled flip true, and only then are backup
#      codes generated (no point handing out recovery codes for a
#      factor that was never confirmed working).
#   3. /auth/mfa/disable — requires re-entering the current password,
#      since removing a security factor is itself a security-lowering
#      action that shouldn't be doable from a merely-still-open tab.
# ---------------------------------------------------------------
class MfaSetupOut(BaseModel):
    secret: str
    provisioning_uri: str


class MfaEnableIn(BaseModel):
    code: str


class MfaEnableOut(BaseModel):
    backup_codes: list[str]


class MfaDisableIn(BaseModel):
    password: str


@app.post("/auth/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("select email, mfa_enabled from users where id = :id"), {"id": user.sub}
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if row.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled — disable it first to re-enroll")

    try:
        secret = generate_totp_secret()
        encrypted = encrypt_secret(secret)
    except MfaConfigError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

    # Overwrites any earlier unconfirmed secret from a previous,
    # never-completed setup attempt — intentional, since only one
    # pending enrollment can be "the real one" at a time.
    await session.execute(
        text("update users set mfa_secret = :secret where id = :id"),
        {"secret": encrypted, "id": user.sub},
    )
    await session.commit()
    return MfaSetupOut(secret=secret, provisioning_uri=provisioning_uri(secret, row.email))


@app.post("/auth/mfa/enable", response_model=MfaEnableOut)
@limiter.limit(LOGIN_RATE_LIMIT)
async def mfa_enable(
    request: Request,
    body: MfaEnableIn,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("select mfa_secret, mfa_enabled from users where id = :id"), {"id": user.sub}
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if row.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled")
    if not row.mfa_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Call /auth/mfa/setup first")

    try:
        secret = decrypt_secret(row.mfa_secret)
    except MfaConfigError:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "MFA verification is temporarily unavailable")

    if not verify_totp(secret, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code — check your authenticator app and try again")

    backup_codes = generate_backup_codes()
    hashed_codes = [hash_backup_code(c) for c in backup_codes]
    await session.execute(
        text("""
            update users
            set mfa_enabled = true, mfa_backup_codes = :codes, mfa_enrolled_at = now()
            where id = :id
        """),
        {"codes": hashed_codes, "id": user.sub},
    )
    await write_audit_log(
        session, user.tenant_id, user.sub, "user.mfa_enabled", "user", user.sub, None, None
    )
    await session.commit()
    # Plaintext codes are returned exactly once — only their hashes
    # are ever persisted, same discipline as a password-reset token.
    return MfaEnableOut(backup_codes=backup_codes)


@app.post("/auth/mfa/disable")
@limiter.limit(LOGIN_RATE_LIMIT)  # same password-guessing-oracle reasoning as /auth/change-password, above
async def mfa_disable(
    request: Request,
    body: MfaDisableIn,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("select password_hash from users where id = :id"), {"id": user.sub}
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    try:
        ph.verify(row.password_hash, body.password)
    except VerifyMismatchError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")

    await session.execute(
        text("""
            update users
            set mfa_enabled = false, mfa_secret = null, mfa_backup_codes = null, mfa_enrolled_at = null
            where id = :id
        """),
        {"id": user.sub},
    )
    await write_audit_log(
        session, user.tenant_id, user.sub, "user.mfa_disabled", "user", user.sub, None, None
    )
    await session.commit()
    return {"message": "MFA has been disabled for this account."}


@app.get("/auth/me")
async def get_current_user_info(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    The JWT payload only carries sub/tenant_id/role — enough to
    authorize requests, not enough to greet someone or show them
    their own company name. This is the endpoint a real frontend
    calls right after login to get that human-readable context.
    Scoped safely by construction: it only ever looks up the
    calling user's own row (by their own JWT-verified sub), never
    takes an ID as a parameter, so there's no way to ask about
    someone else's account through this route.
    """
    result = await session.execute(
        text("""
            select u.email, r.name as role_name, u.full_name, u.title, u.phone as user_phone,
                   t.name as company_name, t.plan, t.website, t.country, t.phone as company_phone,
                   t.logo_data_url, u.is_platform_admin, u.mfa_enabled, u.email_verified
            from users u
            join roles r on r.id = u.role_id
            join tenants t on t.id = u.tenant_id
            where u.id = :user_id
        """),
        {"user_id": user.sub},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    return {
        "user_id": user.sub,
        "email": row.email,
        "role": row.role_name,
        "tenant_id": user.tenant_id,
        "full_name": row.full_name,
        "title": row.title,
        "phone": row.user_phone,
        "company_name": row.company_name,
        "plan": row.plan,
        "website": row.website,
        "country": row.country,
        "company_phone": row.company_phone,
        "logo_data_url": row.logo_data_url,
        "is_platform_admin": row.is_platform_admin,
        "mfa_enabled": row.mfa_enabled,
        "email_verified": row.email_verified,
    }


# ---------------------------------------------------------------
# Company Compliance Profile — the identity a defence buyer needs to
# trust before dealing with a vendor. Two very different exposure
# levels on purpose:
#   - GET /public/companies/{share_token} — NO auth. Meant to be
#     shared with anyone (WhatsApp, email, a pitch link) — so it
#     returns only a name/logo/tagline "card" plus yes/no compliance
#     BADGES, never the actual GST/PAN/TAN/IEC numbers or any contact
#     detail. A leaked share link must never leak a registration
#     number.
#   - GET /companies/{tenant_id}/profile — requires login (any
#     tenant's user, not just the profile owner's — this is the
#     "register to see the full picture" step a real buyer takes
#     before engaging a vendor), and returns the full compliance
#     numbers. Still never returns the profile owner's products,
#     opportunities or any other tenant-private business data — only
#     the identity/compliance fields this migration added.
# ---------------------------------------------------------------
MAX_LOGO_DATA_URL_LEN = 300_000  # ~220KB of binary before base64 overhead — no object storage exists yet, see db/migrations/022
MAX_CUSTOM_FIELDS = 20
MAX_CUSTOM_FIELD_LEN = 500


# Deliberately loose — "linkedin.com/company/x" or "linkedin.com/in/x",
# http or https, with or without www, trailing slash optional. Not
# meant to prove the page exists or belongs to this company (this
# platform has no LinkedIn API integration to check that) — only to
# catch an obviously-wrong paste (a Facebook link, plain text) before
# it's saved and shown to a buyer as this company's own link.
_LINKEDIN_URL_RE = re.compile(
    r"^https?://([a-z]{2,3}\.)?linkedin\.com/(company|in|school)/[^\s]+/?$", re.IGNORECASE
)


class CompanyProfileUpdateIn(BaseModel):
    # Company identity — every field here is admin-editable so a real
    # mistake (a typo in the registered name, a stale phone number)
    # can be fixed without a support ticket. Deliberately does NOT
    # include the tenant's own email/login credentials: those live on
    # `users`, not `tenants`, and are never touched by this route —
    # see get_or_create_government_buyer-style identity reasoning in
    # CLAUDE.md for why an email is the one thing kept out of a
    # self-edit surface like this one.
    name: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    tagline: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    tan_number: Optional[str] = None
    iec_license: Optional[str] = None
    logo_data_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    custom_fields: Optional[dict] = None


_COMPLIANCE_NUMBER_FIELDS = ("gst_number", "pan_number", "tan_number", "iec_license")


def _decrypt_compliance_fields(row: dict) -> dict:
    """
    Mutates and returns the same dict — used by every route that
    reads real gst/pan/tan/iec values back out (the owner's own GET,
    the owner's own PATCH response, and the registered-viewer vetting
    route), so decryption happens in exactly one place rather than
    three copies that could drift. `(gst_number is not null) as
    has_gst`-style presence checks (the public compliance-badge card)
    never call this — they don't need the real value, just whether
    one exists, which works identically on ciphertext.
    """
    for field in _COMPLIANCE_NUMBER_FIELDS:
        if row.get(field):
            row[field] = decrypt_field(row[field])
    return row


def _validate_company_profile_update(body: CompanyProfileUpdateIn):
    if body.name is not None and not body.name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Company name cannot be blank")
    if body.logo_data_url and len(body.logo_data_url) > MAX_LOGO_DATA_URL_LEN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Logo is too large — please use a smaller image")
    if body.linkedin_url and not _LINKEDIN_URL_RE.match(body.linkedin_url.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That doesn't look like a LinkedIn company or profile URL (expected something like "
            "https://www.linkedin.com/company/your-company)",
        )
    if body.custom_fields is not None:
        if len(body.custom_fields) > MAX_CUSTOM_FIELDS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"At most {MAX_CUSTOM_FIELDS} custom fields")
        for k, v in body.custom_fields.items():
            if len(str(k)) > MAX_CUSTOM_FIELD_LEN or len(str(v)) > MAX_CUSTOM_FIELD_LEN:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A custom field's name or value is too long")


@app.get("/company/profile")
async def get_company_profile(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    """Own company's full profile — every field this migration added, for the owning tenant only."""
    result = await session.execute(
        text("""
            select name, website, country, phone, tagline, gst_number, pan_number,
                   tan_number, iec_license, logo_data_url, linkedin_url, share_token, custom_fields
            from tenants where id = :tid
        """),
        {"tid": user.tenant_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    try:
        return _decrypt_compliance_fields(dict(row._mapping))
    except PiiConfigError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@app.patch("/company/profile")
async def update_company_profile(
    body: CompanyProfileUpdateIn,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    _validate_company_profile_update(body)
    existing = await session.execute(text("select custom_fields from tenants where id = :tid"), {"tid": user.tenant_id})
    before = existing.first()
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    new_custom_fields = body.custom_fields if body.custom_fields is not None else dict(before.custom_fields)

    try:
        encrypted_gst = encrypt_field(body.gst_number) if body.gst_number else None
        encrypted_pan = encrypt_field(body.pan_number) if body.pan_number else None
        encrypted_tan = encrypt_field(body.tan_number) if body.tan_number else None
        encrypted_iec = encrypt_field(body.iec_license) if body.iec_license else None
    except PiiConfigError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

    result = await session.execute(
        text("""
            update tenants
            set name = coalesce(:name, name),
                website = coalesce(:website, website),
                country = coalesce(:country, country),
                phone = coalesce(:phone, phone),
                tagline = coalesce(:tagline, tagline),
                gst_number = coalesce(:gst_number, gst_number),
                pan_number = coalesce(:pan_number, pan_number),
                tan_number = coalesce(:tan_number, tan_number),
                iec_license = coalesce(:iec_license, iec_license),
                logo_data_url = coalesce(:logo_data_url, logo_data_url),
                linkedin_url = coalesce(:linkedin_url, linkedin_url),
                custom_fields = CAST(:custom_fields AS jsonb)
            where id = :tid
            returning name, website, country, phone, tagline, gst_number, pan_number,
                      tan_number, iec_license, logo_data_url, linkedin_url, share_token, custom_fields
        """),
        {
            "tid": user.tenant_id,
            "name": body.name.strip() if body.name is not None else None,
            "website": body.website, "country": body.country, "phone": body.phone,
            "tagline": body.tagline,
            "gst_number": encrypted_gst, "pan_number": encrypted_pan,
            "tan_number": encrypted_tan, "iec_license": encrypted_iec,
            "logo_data_url": body.logo_data_url,
            "linkedin_url": body.linkedin_url.strip() if body.linkedin_url is not None else None,
            "custom_fields": json.dumps(new_custom_fields),
        },
    )
    row = result.first()
    await write_audit_log(
        session, user.tenant_id, user.sub, "company_profile.updated",
        "tenant", user.tenant_id, None,
        {"fields_changed": [k for k, v in body.model_dump(exclude={"custom_fields"}).items() if v is not None]},
    )
    await session.commit()
    return _decrypt_compliance_fields(dict(row._mapping))


@app.post("/company/profile/regenerate-share-link")
async def regenerate_company_share_link(
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    Issues a brand-new share_token, instantly invalidating any
    previously-shared link — the revocation mechanism for a link that
    leaked somewhere it shouldn't have.
    """
    result = await session.execute(
        text("""
            update tenants set share_token = encode(gen_random_bytes(16), 'hex')
            where id = :tid
            returning share_token
        """),
        {"tid": user.tenant_id},
    )
    row = result.first()
    await write_audit_log(
        session, user.tenant_id, user.sub, "company_profile.share_link_regenerated",
        "tenant", user.tenant_id, None, None,
    )
    await session.commit()
    return {"share_token": row.share_token}


@app.get("/public/preview-match")
@limiter.limit(PREVIEW_RATE_LIMIT)
async def public_preview_match(request: Request, query: str):
    """
    NO AUTH. The homepage "does this actually work" widget: a visitor
    describes their product in plain language and gets back a real
    count of live tenders it resembles — before creating an account.
    Deliberately returns only counts and capability labels, never a
    programme name, link or contact (see preview_match_for_text's
    docstring) — proof the platform works, not a way to browse the
    real dataset for free.
    """
    query = (query or "").strip()
    if len(query) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Describe your product in a few words")
    if len(query) > 500:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That's a bit long — a sentence or two is enough")
    async with SessionLocal() as session:
        return await preview_match_for_text(session, query)


@app.get("/public/platform-stats")
async def get_public_platform_stats():
    """
    NO AUTH. The homepage's live-numbers strip — real aggregate
    counts (how many tenders, countries, sources, buyers), nothing
    tender-level.

    Deliberately does NOT reuse get_procurement_funnel, even though
    that would be less code: this test suite seeds fixture programmes
    straight into the same shared `programmes` table (see
    tests/test_phase3_matching.py), so on any machine where pytest has
    run, those fixtures sit alongside real ingested tenders. That's
    harmless for the authenticated Procurement Intelligence tab, but
    these four numbers are a public marketing claim on the homepage —
    counting pytest fixtures in them would inflate a number shown to
    prospects. not_a_test_fixture() keeps every public-facing figure to
    genuinely ingested government data only.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            text(f"""
                select count(*) as programme_count,
                       count(distinct country) as country_count,
                       count(distinct source_id) as source_count,
                       count(distinct organization_id) as buyer_count
                from programmes
                where {not_a_test_fixture()}
            """)
        )
        row = result.first()
    return {
        "programme_count": row.programme_count,
        "country_count": row.country_count,
        "source_count": row.source_count,
        "buyer_count": row.buyer_count,
    }


# How many rows a public drill-down will show. Deliberately capped:
# these endpoints exist to prove the numbers above are real, not to
# hand over the dataset to anyone who never signs up — the same
# "proof, not the goods" line /public/preview-match draws.
PUBLIC_STAT_DETAIL_LIMIT = 25

PUBLIC_STAT_METRICS = ("tenders", "countries", "sources", "buyers")


@app.get("/public/stat-detail")
@limiter.limit(PREVIEW_RATE_LIMIT)
async def get_public_stat_detail(request: Request, metric: str):
    """
    NO AUTH. The live data sitting behind each homepage KPI tile, so a
    visitor can click a number and see that it's real rather than
    taking it on faith.

    What each metric deliberately does NOT return: a tender's apply
    link, its procurement contact, or any match/score against a
    specific product. Those are the things an account is actually
    for — this returns only what the number itself is made of.
    """
    if metric not in PUBLIC_STAT_METRICS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"metric must be one of: {', '.join(PUBLIC_STAT_METRICS)}",
        )

    async with SessionLocal() as session:
        if metric == "tenders":
            result = await session.execute(
                text(f"""
                    select p.name, p.country, p.stage, p.response_deadline, s.name as source_name
                    from programmes p
                    left join sources s on s.id = p.source_id
                    where {not_a_test_fixture('p')}
                    order by p.last_updated desc
                    limit :lim
                """),
                {"lim": PUBLIC_STAT_DETAIL_LIMIT},
            )
        elif metric == "countries":
            result = await session.execute(
                text(f"""
                    select country, count(*) as tender_count
                    from programmes
                    where country is not null and {not_a_test_fixture()}
                    group by country
                    order by tender_count desc
                """)
            )
        elif metric == "sources":
            result = await session.execute(
                text(f"""
                    select s.name as source_name, s.url, count(p.id) as tender_count,
                           max(p.last_updated) as last_ingested
                    from sources s
                    left join programmes p
                      on p.source_id = s.id
                     and {not_a_test_fixture('p')}
                    group by s.name, s.url
                    having count(p.id) > 0
                    order by tender_count desc
                """)
            )
        else:  # buyers
            result = await session.execute(
                text(f"""
                    select org.name as organisation, org.country, count(p.id) as tender_count
                    from organizations org
                    join programmes p on p.organization_id = org.id
                    where org.org_type = 'government_body'
                      and {not_a_test_fixture('p')}
                    group by org.name, org.country
                    order by tender_count desc
                    limit :lim
                """),
                {"lim": PUBLIC_STAT_DETAIL_LIMIT},
            )
        rows = [dict(r._mapping) for r in result]

    return {"metric": metric, "rows": rows, "showing": len(rows)}


@app.get("/public/companies/{share_token}")
async def get_public_company_card(share_token: str):
    """
    NO AUTH. The link meant to be pasted into WhatsApp/email/anywhere.
    Deliberately returns only a display card — name, logo, tagline and
    yes/no compliance badges — never a registration number or any
    contact detail, so a leaked link can never leak sensitive data.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                select id, name, tagline, logo_data_url, country, linkedin_url,
                       (gst_number is not null) as has_gst,
                       (pan_number is not null) as has_pan,
                       (tan_number is not null) as has_tan,
                       (iec_license is not null) as has_iec
                from tenants where share_token = :token
            """),
            {"token": share_token},
        )
        row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No company found for this share link")
    return {
        "tenant_id": str(row.id),
        "company_name": row.name,
        "tagline": row.tagline,
        "logo_data_url": row.logo_data_url,
        "country": row.country,
        # A company's own LinkedIn page is public marketing material,
        # not a private contact channel — kept on this no-auth card,
        # unlike email/phone, which stay behind the registration gate.
        "linkedin_url": row.linkedin_url,
        "compliance_badges": {
            "gst": row.has_gst, "pan": row.has_pan,
            "tan": row.has_tan, "iec": row.has_iec,
        },
    }


@app.get("/companies/{tenant_id}/profile")
async def get_company_profile_for_vetting(
    tenant_id: str,
    user: TokenPayload = Depends(get_current_user),
):
    """
    Requires login (any tenant), not just the profile owner's — the
    "register, then see the full picture" step. Deliberately reads
    directly via SessionLocal rather than get_tenant_session: this is
    an intentional, bounded cross-tenant read of identity/compliance
    fields only (never products, opportunities, or any other
    tenant-private business data), the same "shared reference data"
    reasoning documented in CLAUDE.md for programmes/organizations —
    except here it's the vendor's own declared identity, not ingested
    market data.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                select name, website, country, phone, tagline, gst_number, pan_number,
                       tan_number, iec_license, logo_data_url, linkedin_url, custom_fields
                from tenants where id = :tid
            """),
            {"tid": tenant_id},
        )
        row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    try:
        return _decrypt_compliance_fields(dict(row._mapping))
    except PiiConfigError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


# ---------------------------------------------------------------
# Example tenant-scoped routes — unchanged in logic, now actually
# reachable since doi_app has real grants and signup/login exist.
# ---------------------------------------------------------------
class ProductIn(BaseModel):
    name: str
    description: Optional[str] = None
    trl: Optional[int] = None
    # For reference and business-development context only — deliberately
    # NOT a classification/matching input (see app/scoring.py). A
    # certification claim isn't verifiable from this platform's own
    # data, so treating it as a scoring signal would be exactly the
    # kind of unverifiable claim the evidence model here exists to
    # avoid. `products.certifications` existed in the schema since
    # Phase 0 with no way to populate it until now.
    certifications: Optional[list[str]] = None


@app.post("/products", status_code=201)
async def create_product(
    body: ProductIn,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            insert into products (tenant_id, name, description, trl, certifications, created_by)
            values (:tenant_id, :name, :description, :trl, CAST(:certifications AS jsonb), :created_by)
            returning id
        """),
        {
            "tenant_id": user.tenant_id, "name": body.name,
            "description": body.description, "trl": body.trl,
            "certifications": json.dumps(body.certifications or []),
            "created_by": user.sub,
        },
    )
    new_id = result.scalar_one()
    await write_audit_log(
        session, user.tenant_id, user.sub, "product.created",
        "product", str(new_id), None, body.model_dump(),
    )
    await session.commit()
    return {"id": str(new_id)}


@app.get("/products")
async def list_products(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(text("select id, name, trl, certifications, created_at from products"))
    return [dict(row._mapping) for row in result]


@app.delete("/products/{product_id}")
async def delete_product(
    product_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    A real correction mechanism for a real problem: someone enters a
    product with wrong data and needs to remove it, not just leave
    bad data sitting in their account forever. RLS means this query
    can only ever match a product in the caller's own tenant, even
    if a different tenant's real product ID were somehow guessed —
    the DELETE simply matches zero rows and returns 404, same
    tenant-isolation guarantee as everywhere else in this schema.
    Cascades to the product's capabilities and opportunities too
    (see db/migrations/011_product_deletion_cascade.sql) — if the
    product itself was wrong, everything derived from it is
    meaningless too.
    """
    result = await session.execute(
        text("delete from products where id = :id returning id"),
        {"id": product_id},
    )
    deleted = result.first()
    if deleted is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    await write_audit_log(
        session, user.tenant_id, user.sub, "product.deleted",
        "product", product_id, {"product_id": product_id}, None,
    )
    await session.commit()
    return {"status": "deleted"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------
# Phase 1 — Capability Intelligence classification.
#
# /classify runs the rules-based matcher and PERSISTS its top
# suggestions as `ai_suggested` — visible to the tenant, but not
# yet treated as confirmed. An analyst then explicitly confirms
# or rejects each one. This is what makes "human-in-the-loop"
# real rather than a diagram: the classified_by column and the
# evidence.reviewed_by/reviewed_at fields only change via an
# explicit analyst action, never automatically.
# ---------------------------------------------------------------
@app.post("/products/{product_id}/classify")
async def classify_product(
    product_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("select id, name, description from products where id = :id"),
        {"id": product_id},
    )
    product = result.first()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    input_text = f"{product.name} {product.description or ''}"
    candidates = await classify_text(session, input_text)
    candidates = [c for c in candidates if c["score"] >= MINIMUM_SCORE_TO_SUGGEST][:3]

    source_result = await session.execute(
        text("select id from sources where name = 'Internal Rules-Based Capability Classifier'")
    )
    source_row = source_result.first()
    if source_row is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Classifier source not seeded — run db/migrations/002_capability_keywords.sql first",
        )
    source_id = str(source_row.id)

    for c in candidates:
        claim = f"Matched keywords: {', '.join(c['matched_keywords'])} (score {c['score']})"
        ev_result = await session.execute(
            text("""
                insert into evidence
                    (source_id, related_entity_type, related_entity_id, claim, evidence_status, confidence)
                values
                    (:source_id, 'product_capability', :product_id, :claim, 'ai_inferred', :confidence)
                returning id
            """),
            {
                "source_id": source_id, "product_id": product_id,
                "claim": claim, "confidence": c["confidence"],
            },
        )
        evidence_id = str(ev_result.scalar_one())

        await session.execute(
            text("""
                insert into product_capabilities (product_id, capability_id, confidence, classified_by, evidence_id)
                values (:product_id, :capability_id, :confidence, 'ai_suggested', :evidence_id)
                on conflict (product_id, capability_id) do update
                    set confidence = excluded.confidence,
                        classified_by = 'ai_suggested',
                        evidence_id = excluded.evidence_id
            """),
            {
                "product_id": product_id, "capability_id": c["capability_id"],
                "confidence": c["confidence"], "evidence_id": evidence_id,
            },
        )

    await write_audit_log(
        session, user.tenant_id, user.sub, "product.classified",
        "product", product_id, None, {"candidates": candidates},
    )
    await session.commit()
    return {"product_id": product_id, "candidates": candidates}


@app.post("/products/{product_id}/capabilities/{capability_id}/confirm")
async def confirm_product_capability(
    product_id: str,
    capability_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            update product_capabilities
            set classified_by = 'analyst'
            where product_id = :product_id and capability_id = :capability_id
            returning evidence_id
        """),
        {"product_id": product_id, "capability_id": capability_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such capability suggestion for this product")

    if row.evidence_id:
        await session.execute(
            text("update evidence set reviewed_by = :user_id, reviewed_at = now() where id = :eid"),
            {"user_id": user.sub, "eid": str(row.evidence_id)},
        )

    await write_audit_log(
        session, user.tenant_id, user.sub, "product_capability.confirmed",
        "product_capability", product_id, None, {"capability_id": capability_id},
    )
    await session.commit()
    return {"status": "confirmed"}


@app.delete("/products/{product_id}/capabilities/{capability_id}")
async def reject_product_capability(
    product_id: str,
    capability_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            delete from product_capabilities
            where product_id = :product_id and capability_id = :capability_id
            returning capability_id
        """),
        {"product_id": product_id, "capability_id": capability_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such capability link for this product")

    await write_audit_log(
        session, user.tenant_id, user.sub, "product_capability.rejected",
        "product_capability", product_id, {"capability_id": capability_id}, None,
    )
    await session.commit()
    return {"status": "rejected"}


@app.get("/products/{product_id}/capabilities")
async def list_product_capabilities(
    product_id: str,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            select pc.capability_id, ct.code, ct.label, ct.sector,
                   pc.confidence, pc.classified_by,
                   e.claim, e.evidence_status, e.reviewed_by, e.reviewed_at
            from product_capabilities pc
            join capability_taxonomy ct on ct.id = pc.capability_id
            left join evidence e on e.id = pc.evidence_id
            where pc.product_id = :product_id
            order by pc.confidence desc
        """),
        {"product_id": product_id},
    )
    return [dict(row._mapping) for row in result]


# ---------------------------------------------------------------
# Phase 2 — real external data ingestion (SAM.gov).
#
# programmes/organizations/evidence are shared reference tables,
# not tenant-owned (no RLS on them by design — every tenant
# benefits from the same market/programme intelligence). This
# route still requires an authenticated admin so ingestion can't
# be triggered anonymously or by every role, but the data it
# writes becomes visible platform-wide, not just to the caller's
# tenant.
# ---------------------------------------------------------------
class IngestionRunIn(BaseModel):
    naics_codes: Optional[list[str]] = None
    days_back: int = 90


async def _get_credential_status(session: AsyncSession, credential_name: str) -> Optional[dict]:
    result = await session.execute(
        text("select expires_at from api_credentials where credential_name = :name"),
        {"name": credential_name},
    )
    row = result.first()
    if row is None:
        return None
    return compute_status(row.expires_at, date.today())


@app.post("/ingestion/sam-gov/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_sam_gov_ingestion(
    request: Request,
    body: IngestionRunIn,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        result = await run_sam_gov_ingestion(
            session,
            triggered_by_user_id=user.sub,
            naics_codes=body.naics_codes,
            days_back=body.days_back,
        )
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    # Surface the key expiry warning automatically on every real
    # ingestion run — an admin actively using the platform will see
    # this without needing to remember to check a separate endpoint.
    key_status = await _get_credential_status(session, "SAM_GOV_API_KEY")
    result["api_key_status"] = key_status
    return result


class UkFtRunIn(BaseModel):
    days_back: int = 7


@app.post("/ingestion/uk-ft/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_uk_ft_ingestion(
    request: Request,
    body: UkFtRunIn,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    No api_key_status field in the response — unlike SAM.gov, this
    source needs no credential at all, so there's nothing to expire
    and nothing to warn about.
    """
    try:
        result = await run_uk_ft_ingestion(
            session,
            triggered_by_user_id=user.sub,
            days_back=body.days_back,
        )
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


class TedEuRunIn(BaseModel):
    days_back: int = 14


@app.post("/ingestion/ted-eu/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_ted_eu_ingestion(
    request: Request,
    body: TedEuRunIn,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        result = await run_ted_eu_ingestion(
            session,
            triggered_by_user_id=user.sub,
            days_back=body.days_back,
        )
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


class CpppIndiaRunIn(BaseModel):
    max_pages: int = 80  # kept in sync with CPPP_DEFAULT_MAX_PAGES in cppp_india_ingestion.py


@app.post("/ingestion/cppp-india/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_cppp_india_ingestion(
    request: Request,
    body: CpppIndiaRunIn,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    max_pages is the knob that matters here — CPPP serves 10 tenders
    per page and rate-limits, so a run is bounded by pages rather
    than by a date window like the other sources. run_cppp_india_
    ingestion clamps it regardless of what's requested.
    """
    try:
        result = await run_cppp_india_ingestion(
            session,
            triggered_by_user_id=user.sub,
            max_pages=body.max_pages,
        )
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.get("/ingestion/sam-gov/key-status")
async def get_sam_gov_key_status(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    key_status = await _get_credential_status(session, "SAM_GOV_API_KEY")
    if key_status is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No expiry record for SAM_GOV_API_KEY — run db/migrations/005_api_credentials.sql",
        )
    return key_status


class KeyStatusUpdateIn(BaseModel):
    expires_at: str  # YYYY-MM-DD
    notes: Optional[str] = None


@app.patch("/ingestion/sam-gov/key-status")
async def update_sam_gov_key_status(
    body: KeyStatusUpdateIn,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """Call this after rotating to a new SAM.gov key, with its real expiry date."""
    try:
        new_expiry = date.fromisoformat(body.expires_at)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "expires_at must be YYYY-MM-DD")

    result = await session.execute(
        text("""
            update api_credentials
            set expires_at = :expires_at,
                notes = coalesce(:notes, notes),
                updated_at = now()
            where credential_name = 'SAM_GOV_API_KEY'
            returning id, expires_at
        """),
        {"expires_at": new_expiry, "notes": body.notes},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No SAM_GOV_API_KEY credential record exists yet")

    await write_audit_log(
        session, user.tenant_id, user.sub, "api_credential.updated",
        "api_credential", str(row.id), None, {"new_expires_at": body.expires_at},
    )
    await session.commit()
    return compute_status(row.expires_at, date.today())


@app.post("/ingestion/canada-buys/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_canada_buys_ingestion(
    request: Request,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    No request body: CanadaBuys publishes the complete set of
    currently-open tenders as one file, so there is no date window or
    page count to choose — unlike every other source here.
    """
    try:
        result = await run_canada_buys_ingestion(session, triggered_by_user_id=user.sub)
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.post("/ingestion/south-africa/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_south_africa_ingestion(
    request: Request,
    days_back: int = 30,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        result = await run_south_africa_ingestion(session, triggered_by_user_id=user.sub, days_back=days_back)
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.post("/ingestion/colombia/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_colombia_ingestion(
    request: Request,
    days_back: int = 30,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        result = await run_colombia_ingestion(session, triggered_by_user_id=user.sub, days_back=days_back)
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.post("/ingestion/prozorro/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_prozorro_ingestion(
    request: Request,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    # No days_back — this source has no date-range query at all (see
    # app/prozorro_ingestion.py's module docstring); its own
    # max_list_pages/max_detail_fetches defaults apply.
    try:
        result = await run_prozorro_ingestion(session, triggered_by_user_id=user.sub)
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.post("/ingestion/australia/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_australia_ingestion(
    request: Request,
    days_back: int = 90,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        result = await run_australia_ingestion(session, triggered_by_user_id=user.sub, days_back=days_back)
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.post("/ingestion/paraguay/run")
@limiter.limit(INGESTION_RATE_LIMIT)
async def trigger_paraguay_ingestion(
    request: Request,
    days_back: int = 90,
    user: TokenPayload = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        result = await run_paraguay_ingestion(session, triggered_by_user_id=user.sub, days_back=days_back)
    except IngestionConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@app.get("/ingestion/sources/{code}/programmes")
async def list_programmes_for_source(
    code: str,
    limit: int = 50,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    The actual tenders a source has ingested — what the Ingestion
    Control checkboxes reveal, so an admin can see what a run really
    brought in rather than only a record count.

    Reads `programmes`, which is shared reference data with no tenant
    scoping by design (see README) — every tenant sees the same
    ingested market intelligence, so no RLS filter applies here.
    """
    source = next((s for s in INGESTION_SOURCES if s.code == code), None)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown ingestion source '{code}'")

    result = await session.execute(
        text("""
            select pr.external_ref, pr.name, pr.country, pr.stage,
                   pr.naics_code, pr.response_deadline, pr.last_updated,
                   org.name as organization_name
            from programmes pr
            join sources s on s.id = pr.source_id
            left join organizations org on org.id = pr.organization_id
            where s.name = :source_name
            order by pr.last_updated desc
            limit :limit
        """),
        {"source_name": source.display_name, "limit": min(max(limit, 1), 200)},
    )
    rows = [dict(r._mapping) for r in result]

    total = await session.execute(
        text("""
            select count(*) from programmes pr
            join sources s on s.id = pr.source_id
            where s.name = :source_name
        """),
        {"source_name": source.display_name},
    )
    return {
        "code": source.code,
        "display_name": source.display_name,
        "total": total.scalar_one(),
        "programmes": rows,
    }


@app.get("/ingestion/jobs")
async def list_ingestion_jobs(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            select ij.id, s.name as source_name, ij.status, ij.started_at,
                   ij.finished_at, ij.records_ingested, ij.error
            from ingestion_jobs ij
            join sources s on s.id = ij.source_id
            order by ij.started_at desc
            limit 20
        """)
    )
    return [dict(row._mapping) for row in result]


@app.get("/ingestion/scheduler-status")
async def get_scheduler_status(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    job = scheduler.get_job("sam_gov_scheduled_ingestion")

    all_codes = list(DEFENSE_RELEVANT_NAICS.keys())
    total_groups = max(1, (len(all_codes) + NAICS_GROUP_SIZE - 1) // NAICS_GROUP_SIZE)
    rotation_index = await get_rotation_index(session, "SAM_GOV_NAICS_ROTATION")
    next_codes = get_naics_group(all_codes, rotation_index)

    rotation_info = {
        "total_naics_codes_tracked": len(all_codes),
        "group_size": NAICS_GROUP_SIZE,
        "total_groups": total_groups,
        "current_rotation_index": rotation_index,
        "naics_codes_next_run": next_codes,
    }

    if job is None:
        return {"scheduled": False, "message": "No scheduled ingestion job registered.", "rotation": rotation_info}
    return {
        "scheduled": True,
        "interval_hours": SCHEDULED_INGESTION_INTERVAL_HOURS,
        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        "rotation": rotation_info,
    }


@app.get("/ingestion/sources/status")
async def get_ingestion_sources_status(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    The genuinely generalized view — every registered source. Also
    the real fix for a real incident: eTenders South Africa went
    silently dead for 3 weeks (2026-08-24 to 2026-09-15, see this
    file's own header) before a human noticed by hand — nothing here
    would have caught it sooner, because the only "last run" signal
    the frontend had was /ingestion/jobs' `limit 20` GLOBAL feed
    across every source, which a busier source (e.g. SAM.gov's more
    frequent schedule) can push a quiet source clean out of within a
    day or two. That endpoint is left as-is (still useful as a raw
    recent-activity feed) — this route now computes each source's
    OWN last successful run and own last attempt directly, so a dead
    source can never hide behind a busy one.

    `health` is a plain three-way state, not a guess: "never_run" (no
    ingestion_jobs row exists at all for this source),
    "stale" (a success exists, but it's older than health_threshold_
    hours), or "healthy". health_threshold_hours is
    max(48, interval_hours * 3) — tolerates up to two missed
    scheduled cycles before flagging, with a 48h floor so a
    frequently-scheduled source isn't flagged over a single slow run.
    """
    health_rows = await session.execute(
        text("""
            select s.name as source_name,
                   max(ij.finished_at) filter (where ij.status = 'succeeded') as last_success_at,
                   (array_agg(ij.status order by ij.started_at desc))[1] as last_run_status,
                   (array_agg(ij.started_at order by ij.started_at desc))[1] as last_run_at,
                   (array_agg(ij.error order by ij.started_at desc))[1] as last_run_error,
                   -- Same per-source query the health fields above
                   -- already fixed this exact bug for (2026-09) — the
                   -- frontend's Ingestion Control table kept reading
                   -- records_ingested from /ingestion/jobs' own
                   -- GLOBAL `limit 20` feed even after this route
                   -- stopped needing it for status/error/timestamp,
                   -- so a source a busier one pushed out of that
                   -- window (confirmed live: UK Find a Tender and
                   -- eTenders South Africa) silently showed "—"
                   -- records even though the real number was sitting
                   -- right here the whole time.
                   (array_agg(ij.records_ingested order by ij.started_at desc))[1] as last_records_ingested
            from ingestion_jobs ij
            join sources s on s.id = ij.source_id
            group by s.name
        """)
    )
    health_by_source = {row.source_name: row._mapping for row in health_rows}

    now = datetime.now(timezone.utc)
    sources_status = []
    for source in INGESTION_SOURCES:
        job = scheduler.get_job(source.scheduler_job_id)
        health = health_by_source.get(source.display_name)
        last_success_at = health["last_success_at"] if health else None
        hours_since_success = (
            (now - last_success_at).total_seconds() / 3600 if last_success_at else None
        )
        threshold_hours = max(48, source.interval_hours * 3)
        if last_success_at is None:
            health_status = "never_run"
        elif hours_since_success > threshold_hours:
            health_status = "stale"
        else:
            health_status = "healthy"
        last_run_at = health["last_run_at"] if health else None
        sources_status.append({
            "code": source.code,
            "display_name": source.display_name,
            "interval_hours": source.interval_hours,
            "api_key_configured": bool(os.environ.get(source.api_key_env_var)) if source.api_key_env_var else True,
            "scheduled": job is not None,
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "hours_since_last_success": round(hours_since_success, 1) if hours_since_success is not None else None,
            "last_run_status": health["last_run_status"] if health else None,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "last_run_error": health["last_run_error"] if health else None,
            "last_records_ingested": health["last_records_ingested"] if health else None,
            "health_threshold_hours": threshold_hours,
            "health": health_status,
        })
    return {"sources": sources_status}


# ---------------------------------------------------------------
# Backup & DR Phase 1 (2026-09, migration 043) — platform-admin only,
# a stricter gate than every ingestion route above (require_role
# "admin"/"analyst", tenant-scoped). A backup is a full dump of EVERY
# tenant's data in one file; triggering one, or even just knowing
# whether one recently succeeded, is platform infrastructure
# information, not something any signed-up tenant admin should reach.
# ---------------------------------------------------------------
@app.get("/admin/backup/status")
async def get_backup_status(
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    # All three health checks together (Phase 4, 2026-09; retention
    # added 2026-09-25) — "we uploaded a backup recently", "we proved
    # a backup can actually be restored", and "old data is actually
    # being purged on schedule" are three different claims, so one
    # status response answers all three rather than requiring a
    # separate round trip to notice any one side is stale.
    return {
        "backup": await backup_health(session),
        "restore_drill": await restore_drill_health(session),
        "retention_purge": await retention_health(session),
    }


@app.post("/admin/backup/run")
async def trigger_backup(
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    try:
        return await run_backup(session, triggered_by="manual")
    except BackupConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Backup failed: {e}")


@app.post("/admin/backup/verify-restore")
async def trigger_restore_drill(
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    """Manual on-demand version of the weekly scheduled restore drill (Phase 4, 2026-09) — see app/backup.py's run_restore_drill docstring."""
    try:
        return await run_restore_drill(session, triggered_by="manual")
    except BackupConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Restore drill failed: {e}")


@app.post("/admin/retention/run")
async def trigger_retention_purge(
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    """Manual on-demand version of the daily scheduled retention purge (2026-09) — see app/retention.py's own module docstring for the actual retention windows."""
    try:
        return await run_retention_purge(session, triggered_by="manual")
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Retention purge failed: {e}")


# ---------------------------------------------------------------
# Phase 3 — real Programme Intelligence matching.
#
# Deliberately restricted to CONFIRMED capabilities (classified_by
# = 'analyst') — an unreviewed AI suggestion from Phase 1 should
# not silently generate an "opportunity" a customer sees. This
# route is the first thing in the whole project that writes to
# `opportunities`, which has existed since the Phase 0 schema but
# was never populated until now.
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# Customer Intelligence — the original roadmap's Phase 4, deliberately
# deferred in favor of Opportunity Intelligence (see README), now
# revisited. Pure aggregation over programmes/organizations already
# ingested by every source — no new data, no new trust question.
# Shared reference data like /opportunities and /ingestion/sources/
# status, so admin-or-analyst is the right gate, not platform-admin.
# ---------------------------------------------------------------
@app.get("/intelligence/customers")
async def get_customers(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    return {"customers": await list_customers(session)}


# ---------------------------------------------------------------
# Sector Coverage — real programme/opportunity counts per capability
# sector, for the Sector Coverage page's per-sector cards. See
# app/sector_coverage.py for why programme_count is shared/global
# while opportunity_count is scoped to the caller's own tenant, and
# why a programme can legitimately count toward more than one sector.
# ---------------------------------------------------------------
@app.get("/market/sector-coverage")
async def get_sector_coverage(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    return await sector_coverage(session)


@app.get("/intelligence/customers/{organization_id}")
async def get_customer(
    organization_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    detail = await get_customer_detail(session, organization_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No buying organisation found with that id")
    return detail


# ---------------------------------------------------------------
# OEM Intelligence — structural mirror of Customer Intelligence
# above, but aggregated by WINNING organization via contract_awards
# (see db/migrations/016) rather than by buyer. list_oems itself is
# source-agnostic (queries contract_awards directly, no source
# filter) — winner extraction now covers 7 of 9 sources: TED, UK
# Find a Tender, SAM.gov, CanadaBuys, SECOP II Colombia, ProZorro
# Ukraine and AusTender Australia. Only CPPP (HTML-scraped, no
# structured award field) and eTenders South Africa (no award-stage
# data in what's been sampled live) remain unaddressed — see
# CLAUDE.md's OEM Intelligence section for what was verified for
# each.
# ---------------------------------------------------------------
@app.get("/intelligence/oems")
async def get_oems(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    return {"oems": await list_oems(session)}


@app.get("/intelligence/oems/{organization_id}")
async def get_oem(
    organization_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    detail = await get_oem_detail(session, organization_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No OEM found with that id")
    return detail


# ---------------------------------------------------------------
# Procurement Intelligence — shared reference data, same access
# pattern as Customer/OEM Intelligence above. Deliberately distinct
# from opportunities.stage (the tenant's own sales pipeline): this
# is the government's own procurement lifecycle for the programme
# itself, see app/procurement_intelligence.py's module docstring.
# ---------------------------------------------------------------
@app.get("/intelligence/procurement")
async def get_procurement_intelligence(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    return await get_procurement_funnel(session)


# ---------------------------------------------------------------
# Market Intelligence — the Global Markets page's real data source.
# Same shared-reference-data, no-tenant-scoping pattern as Customer/
# OEM/Procurement Intelligence above (see app/market_intelligence.py
# module docstring for what this deliberately does NOT invent).
# ---------------------------------------------------------------
@app.get("/intelligence/markets")
async def get_market_intelligence(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    return {"markets": await list_markets(session)}


# ---------------------------------------------------------------
# Generic drill-down: one real reason a user asked for this
# (2026-09) — every intelligence module shows an aggregate number
# ("US 403 tenders", "ROU 2 tenders" on Market Intelligence) with no
# way to see the actual tenders behind it, forcing a trip to Taxonomy
# Admin or an unrelated search to verify a count is real. This is
# deliberately ONE shared, filterable endpoint rather than a separate
# bespoke route per module — Market Intelligence filters by country,
# Customer Intelligence by organization_id (the buyer), OEM
# Intelligence by winner_organization_id (via contract_awards),
# Procurement Intelligence by stage/source_name — all the same
# underlying `programmes` rows, just sliced differently, so the
# numbers shown here can never drift from what each module already
# computed from the exact same table.
#
# Deliberately returns NO contact fields — programmes.contact_* is
# under a hard scope limit already established for Tender Briefing
# (see migration 029's own note in this file): read only via the ONE
# programme/opportunity it belongs to, never listed, searched, or
# aggregated across tenders. A browse/list view is exactly the
# aggregation that rule exists to prevent, so this query does not
# select those columns at all — not merely hides them in the response.
# ---------------------------------------------------------------
@app.get("/intelligence/programmes/browse")
async def browse_programmes(
    country: Optional[str] = None,
    organization_id: Optional[str] = None,
    winner_organization_id: Optional[str] = None,
    source_name: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = 200,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    limit = max(1, min(limit, 500))
    conditions = [not_a_test_fixture("p")]
    params: dict = {"limit": limit}
    if country:
        conditions.append("p.country = :country")
        params["country"] = country
    if organization_id:
        conditions.append("p.organization_id = :organization_id")
        params["organization_id"] = organization_id
    if source_name:
        conditions.append("s.name = :source_name")
        params["source_name"] = source_name
    if stage:
        conditions.append("p.stage = :stage")
        params["stage"] = stage
    award_join = ""
    if winner_organization_id:
        award_join = "join contract_awards ca on ca.programme_id = p.id"
        conditions.append("ca.winner_organization_id = :winner_organization_id")
        params["winner_organization_id"] = winner_organization_id
    where_sql = " and ".join(conditions)

    count_result = await session.execute(
        text(f"""
            select count(distinct p.id) as total
            from programmes p
            left join sources s on s.id = p.source_id
            {award_join}
            where {where_sql}
        """),
        params,
    )
    total = count_result.scalar_one()

    rows = await session.execute(
        text(f"""
            select distinct p.id, p.name, p.country, p.stage, p.naics_code,
                   p.response_deadline, p.ui_link, s.name as source_name,
                   org.name as organization_name, p.last_updated
            from programmes p
            left join sources s on s.id = p.source_id
            left join organizations org on org.id = p.organization_id
            {award_join}
            where {where_sql}
            order by p.last_updated desc
            limit :limit
        """),
        params,
    )
    programmes = [dict(r._mapping) for r in rows]
    return {"total": total, "returned": len(programmes), "programmes": programmes}


# ---------------------------------------------------------------
# Competitor Intelligence — the one TENANT-SCOPED module of this
# group. Depends on the CALLING tenant's own confirmed capabilities,
# so (unlike Customer/OEM/Procurement Intelligence) two different
# tenants genuinely see different results here.
# ---------------------------------------------------------------
@app.get("/intelligence/competitors")
async def get_competitors(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    return await list_competitors(session)


# ---------------------------------------------------------------
# Partner Matching — the teaming half of engine 05, and the exact
# inverse of Competitor Intelligence above: same award data, but
# filtered to companies winning from the SAME buyers in DIFFERENT
# capabilities. Tenant-scoped for the same reason competitors are.
# ---------------------------------------------------------------
@app.get("/intelligence/partners")
async def get_partners(
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    return await list_partners(session)


@app.post("/products/{product_id}/match-programmes")
async def match_programmes_for_product(
    product_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    product_check = await session.execute(
        text("select id from products where id = :id"), {"id": product_id}
    )
    if product_check.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    match_result = await match_product_to_programmes(session, product_id)
    matches = match_result["matches"]

    persisted = []
    for m in matches:
        # Next-Best-Action: computed only for the INITIAL insert
        # (see the on-conflict clause below, which deliberately never
        # touches next_action) — a routine re-match refreshing score/
        # confidence must never overwrite a suggestion a human has
        # since acted on or manually replaced.
        initial_next_action = suggest_next_action(
            "lead", m["confidence"], m.get("response_deadline"),
            set_aside_code=m.get("set_aside_code"), set_aside_description=m.get("set_aside_description"),
            has_owner=False,  # a freshly-matched opportunity has no owner yet by construction
        )
        upsert_result = await session.execute(
            text("""
                insert into opportunities
                    (tenant_id, product_id, programme_id, organization_id, score, confidence, stage, next_action)
                values
                    (:tenant_id, :product_id, :programme_id, :organization_id, :score, :confidence, 'lead', :next_action)
                on conflict (product_id, programme_id) do update
                    set score = excluded.score,
                        confidence = excluded.confidence,
                        updated_at = now()
                returning id, stage
            """),
            {
                "tenant_id": user.tenant_id, "product_id": product_id,
                "programme_id": m["programme_id"], "organization_id": m["organization_id"],
                "score": m["total_score"], "confidence": m["confidence"],
                "next_action": initial_next_action,
            },
        )
        row = upsert_result.first()
        persisted.append({**m, "opportunity_id": str(row.id), "stage": row.stage})

    await write_audit_log(
        session, user.tenant_id, user.sub, "product.matched_programmes",
        "product", product_id, None, {"match_count": len(persisted)},
    )
    await session.commit()
    return {"product_id": product_id, "matches": persisted, "trace": match_result["trace"]}


# ---------------------------------------------------------------
# Engagement Intelligence — the original roadmap's Phase 4. The
# schema already had everything this needs (opportunities.stage is a
# full pipeline enum, next_action/owner_user_id/due_date all
# existed) since the Phase 0 schema; nothing wrote to any of them
# until this route. audit_log's own column comment even names the
# action this logs ('opportunity.stage_changed') — the pipeline was
# designed for this from the start, just never wired up.
# ---------------------------------------------------------------
OPPORTUNITY_STAGES = (
    "lead", "qualified", "technical_discussion", "nda", "demo",
    "trial", "evaluation", "rfi", "rfp", "proposal", "negotiation",
    "won", "lost",
)


class OpportunityUpdateIn(BaseModel):
    stage: Optional[str] = None
    next_action: Optional[str] = None
    owner_user_id: Optional[str] = None
    # owner_user_id follows the same "None means not part of this
    # PATCH" rule as checklist_state below — a real consequence of
    # that rule the team-workload feature ran into immediately: a
    # picker offering "Unassigned" could not actually unassign an
    # owner by sending owner_user_id: null, since that reads as "no
    # change" like everywhere else on this model. This is the explicit
    # opt-in to actually clear it, rather than overloading None to mean
    # two different things depending on which field it's on.
    clear_owner: bool = False
    due_date: Optional[str] = None  # YYYY-MM-DD
    # When true (the default) and `stage` is being changed without an
    # explicit next_action in the same request, a fresh suggestion is
    # computed via app.next_best_action — set false to change stage
    # without touching whatever next_action already says.
    auto_suggest_next_action: bool = True
    # Contract onboarding checklist tick state, keyed by the frontend's
    # static template item ids ({"legal_contract_signed": true, ...}).
    # The frontend always sends the complete map (it knows every item
    # id from its own template), so this is a plain replace, not a
    # merge — None means "not part of this PATCH", not "clear it".
    checklist_state: Optional[dict] = None


@app.patch("/opportunities/{opportunity_id}")
async def update_opportunity(
    opportunity_id: str,
    body: OpportunityUpdateIn,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    existing = await session.execute(
        text("""
            select o.stage, o.next_action, o.owner_user_id, o.due_date,
                   o.confidence, o.checklist_state, p.response_deadline,
                   -- Path to Contract — the fields a user needs to actually
                   -- reach and apply for the tender, not just see that it
                   -- exists. p.ui_link is the real apply/detail URL every
                   -- normalizer has computed since day one but the schema
                   -- never had a column for (see db/migrations/018).
                   p.name as programme_name, p.ui_link, p.set_aside_code, p.set_aside_description,
                   -- Procurement contact for THIS specific tender only.
                   -- Read here, on the one opportunity being viewed —
                   -- never listed or searched across programmes. See
                   -- db/migrations/017 for why that boundary exists.
                   p.contact_name, p.contact_email, p.contact_email_secondary, p.contact_phone, p.contact_address,
                   -- Tender Briefing: what the user reads BEFORE being
                   -- sent off to SAM.gov/TED/etc. Buyer, country, source
                   -- and the government's own procurement stage are all
                   -- already stored — they just were never returned
                   -- here, so the briefing can be assembled without any
                   -- new ingestion.
                   p.country, p.stage as programme_stage, p.naics_code, p.organization_id,
                   org.name as organization_name, s.name as source_name,
                   -- How many times this tender has been opened on its
                   -- source portal. The first open is an audit_log
                   -- event; repeats only move this counter (see
                   -- db/migrations/024 for why).
                   o.tender_opened_count, o.tender_last_opened_at
            from opportunities o
            left join programmes p on p.id = o.programme_id
            left join organizations org on org.id = p.organization_id
            left join sources s on s.id = p.source_id
            where o.id = :id
        """),
        {"id": opportunity_id},
    )
    before = existing.first()
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")

    if body.stage is not None and body.stage not in OPPORTUNITY_STAGES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"stage must be one of: {', '.join(OPPORTUNITY_STAGES)}",
        )

    # owner_user_id has only a global FK (references users(id), any
    # tenant) — see db/migrations for opportunities_owner_user_id_fkey.
    # `users` itself carries no RLS (see /team/members' own docstring),
    # so nothing at the database level stops this from being set to a
    # DIFFERENT tenant's user id. That's not just cosmetic:
    # /engagement/team-workload joins owner_user_id straight to
    # users.full_name/email with no tenant filter, so an unvalidated
    # cross-tenant id set here would disclose that other user's name
    # and email on THIS tenant's own dashboard. Validated explicitly
    # here, the same discipline /team/members already documents.
    if body.owner_user_id is not None and not body.clear_owner:
        owner_check = await session.execute(
            text("select 1 from users where id = :owner_id and tenant_id = :tid"),
            {"owner_id": body.owner_user_id, "tid": user.tenant_id},
        )
        if owner_check.first() is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "owner_user_id must be a member of your own team")

    new_due_date = before.due_date
    if body.due_date is not None:
        try:
            # asyncpg needs a real date object for a `date` column —
            # raw text() with a plain string fails with an opaque
            # "'str' object has no attribute 'toordinal'" DBAPIError,
            # not a clean validation error, so this conversion has to
            # happen here rather than being left to the driver.
            new_due_date = date.fromisoformat(body.due_date)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "due_date must be YYYY-MM-DD")

    new_owner_for_update = None if body.clear_owner else body.owner_user_id
    new_stage = body.stage if body.stage is not None else before.stage
    if body.next_action is not None:
        new_next_action = body.next_action
    elif body.stage is not None and body.auto_suggest_next_action:
        # A stage change with no explicit next_action gets a fresh
        # rules-based suggestion for the NEW stage — otherwise moving
        # an opportunity from 'lead' to 'won' would leave a stale
        # "reach out to confirm requirement fit" sitting there.
        new_owner_user_id = None if body.clear_owner else (body.owner_user_id or before.owner_user_id)
        new_next_action = suggest_next_action(
            new_stage, before.confidence, before.response_deadline,
            set_aside_code=before.set_aside_code, set_aside_description=before.set_aside_description,
            has_owner=new_owner_user_id is not None,
            due_date=new_due_date.isoformat() if new_due_date else None,
        )
    else:
        new_next_action = before.next_action

    new_checklist_state = body.checklist_state if body.checklist_state is not None else before.checklist_state

    result = await session.execute(
        text("""
            update opportunities
            set stage = :stage,
                next_action = :next_action,
                owner_user_id = case when :clear_owner then null else coalesce(:owner_user_id, owner_user_id) end,
                due_date = :due_date,
                checklist_state = CAST(:checklist_state AS jsonb),
                updated_at = now()
            where id = :id
            returning id, stage, next_action, owner_user_id, due_date, score, confidence, checklist_state
        """),
        {
            "id": opportunity_id,
            "stage": new_stage,
            "next_action": new_next_action,
            "owner_user_id": new_owner_for_update,
            "clear_owner": body.clear_owner,
            "due_date": new_due_date,
            "checklist_state": json.dumps(new_checklist_state),
        },
    )
    updated = result.first()

    before_state = {
        "stage": before.stage, "next_action": before.next_action,
        "owner_user_id": str(before.owner_user_id) if before.owner_user_id else None,
    }
    after_state = {
        "stage": updated.stage, "next_action": updated.next_action,
        "owner_user_id": str(updated.owner_user_id) if updated.owner_user_id else None,
    }
    # An empty/no-op PATCH (nothing actually changed) is not a real
    # engagement event — logging it would pollute the history timeline
    # with entries that don't represent anything happening.
    if before_state != after_state:
        await write_audit_log(
            session, user.tenant_id, user.sub, "opportunity.stage_changed",
            "opportunity", opportunity_id, before_state, after_state,
        )
    await session.commit()

    row = {
        "confidence": updated.confidence,
        "set_aside_code": before.set_aside_code,
        "set_aside_description": before.set_aside_description,
        "response_deadline": before.response_deadline,
        "organization_id": before.organization_id,
    }
    await _attach_fit_scores(session, [row])

    return {
        **dict(updated._mapping),
        "contact_name": before.contact_name,
        "contact_email": before.contact_email,
        "contact_email_secondary": before.contact_email_secondary,
        "contact_phone": before.contact_phone,
        "contact_address": before.contact_address,
        "programme_name": before.programme_name,
        "ui_link": before.ui_link,
        "response_deadline": before.response_deadline,
        "set_aside_code": before.set_aside_code,
        "set_aside_description": before.set_aside_description,
        "checklist_state": updated.checklist_state,
        "country": before.country,
        "programme_stage": before.programme_stage,
        "classification_code": before.naics_code,
        "organization_name": before.organization_name,
        "source_name": before.source_name,
        "tender_opened_count": before.tender_opened_count,
        "tender_last_opened_at": before.tender_last_opened_at,
        "fit_score": row["fit_score"],
    }


@app.post("/opportunities/{opportunity_id}/tender-opened")
async def record_tender_opened(
    opportunity_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    Records that the user actually opened this tender on the source
    portal, from the Tender Briefing.

    The FIRST open is written to audit_log like every other engagement
    event, so it shows up in the same Engagement History timeline as
    stage changes — no separate events table, and no separate notion
    of "activity". Re-opening the same tender only advances
    `tender_opened_count`: going back to a tender you already looked
    at is the same event happening again, not a new one, and a row per
    visit would bury real pipeline activity under repeated "opened
    tender" lines in the one timeline a user reads to understand what
    happened here.

    Deliberately a distinct action from a stage change: opening a
    tender to read it is not the same as deciding to pursue it, and
    collapsing the two would quietly advance someone's pipeline just
    because they clicked a link.
    """
    # The update both reads and writes in one statement so two rapid
    # clicks can't both see count = 0 and both write a history row.
    # `returning` gives the value BEFORE this increment, which is what
    # decides whether this open is the first one.
    result = await session.execute(
        text("""
            update opportunities
            set tender_opened_count = tender_opened_count + 1,
                tender_last_opened_at = now()
            where id = :id
            returning stage, tender_opened_count, tender_opened_count - 1 as previous_count
        """),
        {"id": opportunity_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")

    first_open = row.previous_count == 0
    if first_open:
        await write_audit_log(
            session, user.tenant_id, user.sub, "opportunity.tender_opened",
            "opportunity", opportunity_id, None,
            {"stage": row.stage, "event": "Opened the tender on the source portal"},
        )
    await session.commit()
    return {
        "opportunity_id": opportunity_id,
        "recorded": True,
        "logged_to_history": first_open,
        "tender_opened_count": row.tender_opened_count,
    }


@app.get("/engagement/summary")
async def get_engagement_summary(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    How much this tenant has actually done through the platform —
    every figure is a server-recorded event rather than something the
    browser tallied and could inflate. Both tables here are already
    tenant-scoped by RLS, so this needs no tenant filter of its own.

    `tenders_opened` counts distinct tenders from the counter column
    rather than from audit_log: audit_log only carries the first open
    of each tender now, and `tender_open_events` is the repeat-visit
    total that counter exists to hold.
    """
    opened = await session.execute(
        text("""
            select count(*) as tenders_opened,
                   coalesce(sum(tender_opened_count), 0) as tender_open_events
            from opportunities
            where tender_opened_count > 0
        """)
    )
    opened_row = opened.first()
    worked = await session.execute(
        text("""
            select count(distinct entity_id) as opportunities_worked
            from audit_log
            where entity_type = 'opportunity' and action = 'opportunity.stage_changed'
        """)
    )
    total = await session.execute(text("select count(*) as c from opportunities"))
    return {
        "tenders_opened": opened_row.tenders_opened or 0,
        "tender_open_events": int(opened_row.tender_open_events or 0),
        "opportunities_worked": worked.scalar_one() or 0,
        "total_opportunities": total.scalar_one(),
    }

# ---------------------------------------------------------------
# Team visibility — the real gap a Report Intel review found in
# Engagement Intelligence: `opportunities.owner_user_id` has existed
# since the Phase 0 schema and was always settable via PATCH, but
# nothing ever showed WHO a tenant's teammates actually are (no picker
# existed — the field was write-only in practice), and nothing ever
# surfaced whether an assigned follow-up had quietly gone overdue.
# Both fixed here with what the schema already had — no new tables.
# ---------------------------------------------------------------
@app.get("/team/members")
async def list_team_members(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    Powers an owner-assignment picker. `users` carries no RLS policy
    (confirmed — unlike opportunities/products/audit_log, it is NOT
    tenant-isolated at the database level), so `tenant_id` is filtered
    explicitly here rather than relied on implicitly, the same
    discipline every other query against this table in this file
    already follows.
    """
    result = await session.execute(
        text("""
            select id, full_name, email, title
            from users
            where tenant_id = :tid and is_active = true
            order by coalesce(full_name, email)
        """),
        {"tid": user.tenant_id},
    )
    return [dict(row._mapping) for row in result]


@app.get("/engagement/team-workload")
async def get_team_workload(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    Real team visibility, not a browser tally: every open opportunity
    (stage not won/lost) grouped by its owner — including an explicit
    "Unassigned" bucket, which is its own useful signal (work nobody
    has claimed) — with how many are overdue against their own
    due_date. This is the one place a manager can see workload
    distribution across a team without opening every row.
    """
    result = await session.execute(
        text("""
            select o.owner_user_id, u.full_name, u.email,
                   count(*) as open_count,
                   count(*) filter (where o.due_date is not null and o.due_date < current_date) as overdue_count
            from opportunities o
            -- users carries no RLS (see /team/members' own docstring),
            -- so the tenant_id check belongs in the JOIN itself, not
            -- assumed from o already being RLS-scoped — an owner_user_id
            -- pointing to a different tenant's user (should no longer
            -- be possible going forward, see PATCH /opportunities'
            -- own validation, but any such row from before that
            -- existed) must never disclose that user's name/email here.
            left join users u on u.id = o.owner_user_id and u.tenant_id = :tid
            where o.stage not in ('won', 'lost')
            group by o.owner_user_id, u.full_name, u.email
            order by overdue_count desc, open_count desc
        """),
        {"tid": user.tenant_id},
    )
    rows = [dict(row._mapping) for row in result]
    for row in rows:
        row["owner_user_id"] = str(row["owner_user_id"]) if row["owner_user_id"] else None
        row["display_name"] = row["full_name"] or row["email"] or "Unassigned"
    return {"team": rows}


# ---------------------------------------------------------------
# Report Intel — all ten engines' real output for ONE product.
#
# Gated on a product that finished the cycle (see
# app/product_intel_report's module docstring): a half-run pipeline
# produces a report of empty blocks, which reads as the engines being
# broken rather than the product not having got there yet. The gate is
# returned as data (`cycle`), not as a 403 — the caller is entitled to
# see exactly which step is missing.
# ---------------------------------------------------------------
@app.get("/products/{product_id}/intel-report")
async def get_product_intel_report(
    product_id: str,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    report = await build_product_intel_report(session, product_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return report


@app.get("/opportunities/{opportunity_id}/history")
async def get_opportunity_history(
    opportunity_id: str,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    """
    The engagement timeline for one opportunity — every stage/action
    change, reusing audit_log rather than a new events table, since
    that table already exists for exactly this kind of before/after
    record.
    """
    existing = await session.execute(text("select id from opportunities where id = :id"), {"id": opportunity_id})
    if existing.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")

    result = await session.execute(
        text("""
            select action, before_state, after_state, created_at
            from audit_log
            where entity_type = 'opportunity' and entity_id = :id
            order by created_at desc
        """),
        {"id": opportunity_id},
    )
    return {"opportunity_id": opportunity_id, "history": [dict(row._mapping) for row in result]}


async def _attach_fit_scores(session: AsyncSession, rows: list[dict]) -> None:
    """
    Mutates each row dict in place, adding a 'fit_score' key (see
    app/fit_score.py for the actual scoring logic — this is only the
    I/O side). Buyer award-value/competitiveness stats are fetched in
    ONE batched query across every distinct organization_id in
    `rows`, not one query per row, so this stays cheap even on a full
    opportunities list.
    """
    from app.fit_score import compute_fit_score

    org_ids = list({r["organization_id"] for r in rows if r.get("organization_id")})
    buyer_stats: dict[str, dict] = {}
    if org_ids:
        result = await session.execute(
            text("""
                select p.organization_id,
                       avg(ca.value_amount) as avg_value,
                       (array_agg(ca.value_currency) filter (where ca.value_currency is not null))[1] as currency,
                       count(distinct ca.winner_organization_id) as distinct_winners
                from contract_awards ca
                join programmes p on p.id = ca.programme_id
                where p.organization_id = any(:org_ids)
                group by p.organization_id
            """),
            {"org_ids": org_ids},
        )
        for row in result:
            buyer_stats[str(row.organization_id)] = {
                "avg_value": float(row.avg_value) if row.avg_value is not None else None,
                "currency": row.currency,
                "distinct_winners": row.distinct_winners or None,
            }

    for r in rows:
        stats = buyer_stats.get(str(r.get("organization_id"))) or {"avg_value": None, "currency": None, "distinct_winners": None}
        r["fit_score"] = compute_fit_score(
            confidence=r.get("confidence"),
            set_aside_code=r.get("set_aside_code"),
            set_aside_description=r.get("set_aside_description"),
            response_deadline=str(r["response_deadline"]) if r.get("response_deadline") else None,
            buyer_avg_award_value=stats["avg_value"],
            buyer_award_currency=stats["currency"],
            buyer_distinct_winner_count=stats["distinct_winners"],
        )


@app.get("/opportunities")
async def list_opportunities(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            select o.id, o.score, o.confidence, o.stage, o.next_action, o.created_at,
                   o.programme_id, o.product_id, o.organization_id, p.name as product_name, pr.name as programme_name, org.name as organization_name,
                   pr.set_aside_code, pr.set_aside_description, pr.response_deadline,
                   pr.country, pr.naics_code, s.name as source_name, pr.ui_link,
                   -- Whether this tender is actually actionable from
                   -- what the source itself published, not whether it
                   -- matched well — a high-scoring match with neither
                   -- an apply link nor a procurement contact is a real,
                   -- known gap (the source's own notice is incomplete,
                   -- not a platform bug), and the list needs to say so
                   -- rather than let it look identical to a fully
                   -- actionable one (2026-09, a real user question:
                   -- "what's the use of a lead with no way to act on
                   -- it?").
                   (pr.ui_link is not null or pr.contact_name is not null or pr.contact_email is not null) as has_action_path,
                   -- The GOVERNMENT'S own lifecycle stage for the
                   -- tender itself — returned on the list so a high
                   -- match score never gets mistaken for a live
                   -- opportunity when the source already shows it
                   -- awarded. A real case that prompted this: an
                   -- LVCR-classified product matched an already-
                   -- awarded programme at a high score — genuinely
                   -- useful for Competitor Intelligence, genuinely
                   -- NOT something to hand a customer as something to
                   -- go bid on.
                   pr.stage as programme_stage,
                   -- Owner + due date, added so the dashboard can
                   -- show WHO is working an opportunity and whether
                   -- it's overdue — both columns existed in the
                   -- schema since Phase 0 and were writable via PATCH,
                   -- but never once returned on the list, so there
                   -- was no way to actually see them without opening
                   -- every row individually.
                   o.owner_user_id, o.due_date,
                   -- Returned on the LIST, not just the detail, so the
                   -- dashboard can say WHICH tenders were opened and how
                   -- often without a request per row. A tender someone
                   -- went back to three times is a stronger signal of
                   -- real intent than its match score alone.
                   o.tender_opened_count, o.tender_last_opened_at
            from opportunities o
            join products p on p.id = o.product_id
            left join programmes pr on pr.id = o.programme_id
            left join organizations org on org.id = o.organization_id
            left join sources s on s.id = pr.source_id
            order by o.score desc nulls last
        """)
    )
    rows = [dict(row._mapping) for row in result]
    await _attach_fit_scores(session, rows)

    # Sector tags per row — for the Sector Coverage page's click-
    # through ("show me this sector's opportunities"). Index built
    # ONCE for the whole list, not once per row (same batching
    # discipline as _attach_fit_scores above).
    from app.sector_coverage import build_sector_index, resolve_programme_sectors
    sector_index, cap_sector = await build_sector_index(session)
    for r in rows:
        r["sectors"] = resolve_programme_sectors(r.get("naics_code"), sector_index, cap_sector)

    return rows


# ---------------------------------------------------------------
# "What's new" — the actual mechanism behind an honest "early
# visibility" claim. `tenants.opportunities_last_viewed_at` is a
# server-side checkpoint the tenant never writes to directly; it
# only advances when they genuinely call this endpoint, which is
# what makes "you saw this first" a provable timestamp rather than
# a marketing line. Deliberately separate from GET /opportunities,
# which remains the unfiltered, non-mutating "see everything" view.
# ---------------------------------------------------------------
@app.get("/opportunities/new")
async def list_new_opportunities(
    mark_seen: bool = True,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    checkpoint_result = await session.execute(
        text("select opportunities_last_viewed_at from tenants where id = :tid"),
        {"tid": user.tenant_id},
    )
    checkpoint_row = checkpoint_result.first()
    last_viewed = checkpoint_row.opportunities_last_viewed_at if checkpoint_row else None

    if last_viewed is None:
        result = await session.execute(
            text("""
                select o.id, o.score, o.confidence, o.stage, o.created_at,
                       p.name as product_name, pr.name as programme_name, org.name as organization_name,
                       pr.set_aside_code, pr.set_aside_description, pr.response_deadline
                from opportunities o
                join products p on p.id = o.product_id
                left join programmes pr on pr.id = o.programme_id
                left join organizations org on org.id = o.organization_id
                order by o.score desc nulls last
            """)
        )
    else:
        result = await session.execute(
            text("""
                select o.id, o.score, o.confidence, o.stage, o.created_at,
                       p.name as product_name, pr.name as programme_name, org.name as organization_name,
                       pr.set_aside_code, pr.set_aside_description, pr.response_deadline
                from opportunities o
                join products p on p.id = o.product_id
                left join programmes pr on pr.id = o.programme_id
                left join organizations org on org.id = o.organization_id
                where o.created_at > :last_viewed
                order by o.score desc nulls last
            """),
            {"last_viewed": last_viewed},
        )

    new_opportunities = [dict(row._mapping) for row in result]

    if mark_seen:
        await session.execute(
            text("update tenants set opportunities_last_viewed_at = now() where id = :tid"),
            {"tid": user.tenant_id},
        )
        await session.commit()

    return {
        "new_count": len(new_opportunities),
        "opportunities": new_opportunities,
        "previously_viewed_at": last_viewed.isoformat() if last_viewed else None,
    }


# ---------------------------------------------------------------
# Platform admin — capability taxonomy management.
#
# GET is open to any authenticated user (read-only, not sensitive —
# understanding what's classifiable helps everyone). Every write
# route requires require_platform_admin, not require_role("admin"),
# because this is shared global data every tenant's classifier
# reads from — see the migration comment and require_platform_admin
# docstring for why gating this behind tenant-scoped admin would be
# a real security bug, not just an oversight.
# ---------------------------------------------------------------
@app.get("/admin/taxonomy")
async def list_taxonomy(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            select ct.id, ct.code, ct.label, ct.sector, ct.description,
                   count(k.id) as keyword_count
            from capability_taxonomy ct
            left join capability_taxonomy_keywords k on k.capability_id = ct.id
            group by ct.id, ct.code, ct.label, ct.sector, ct.description
            order by ct.sector, ct.label
        """)
    )
    rows = [dict(row._mapping) for row in result]
    # Real contribution numbers per capability (2026-09) — see
    # app/sector_coverage.py's capability_contribution docstring for
    # why this exists: a user correctly asked how to SEE the effect of
    # a mapping change (migration 041) instead of needing a manual DB
    # check every time. Missing from the dict entirely (not a 0) means
    # this capability currently has no classification-code mapping at
    # all — genuinely different from "mapped, but nothing has matched
    # it yet".
    contribution = await capability_contribution(session)
    for row in rows:
        c = contribution.get(row["code"])
        row["mapping_count"] = c["mapping_count"] if c else 0
        row["programme_count"] = c["programme_count"] if c else 0
        row["contract_award_count"] = c["contract_award_count"] if c else 0
    return rows


@app.get("/admin/taxonomy/{capability_id}/keywords")
async def list_taxonomy_keywords(
    capability_id: str,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            select id, keyword, weight
            from capability_taxonomy_keywords
            where capability_id = :cid
            order by weight desc, keyword
        """),
        {"cid": capability_id},
    )
    return [dict(row._mapping) for row in result]


class TaxonomyIn(BaseModel):
    code: str
    label: str
    sector: str
    description: Optional[str] = None


@app.post("/admin/taxonomy", status_code=201)
async def create_taxonomy_entry(
    body: TaxonomyIn,
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    existing = await session.execute(
        text("select id from capability_taxonomy where code = :code"), {"code": body.code}
    )
    if existing.first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Taxonomy code '{body.code}' already exists")

    result = await session.execute(
        text("""
            insert into capability_taxonomy (code, label, sector, description)
            values (:code, :label, :sector, :description)
            returning id
        """),
        {"code": body.code, "label": body.label, "sector": body.sector, "description": body.description},
    )
    new_id = result.scalar_one()
    await write_audit_log(
        session, user.tenant_id, user.sub, "taxonomy.created",
        "capability_taxonomy", str(new_id), None, body.model_dump(),
    )
    await session.commit()
    return {"id": str(new_id)}


class TaxonomyKeywordIn(BaseModel):
    keyword: str
    weight: int = 1


@app.post("/admin/taxonomy/{capability_id}/keywords", status_code=201)
async def add_taxonomy_keyword(
    capability_id: str,
    body: TaxonomyKeywordIn,
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    if not (1 <= body.weight <= 5):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "weight must be between 1 and 5")

    capability_check = await session.execute(
        text("select id from capability_taxonomy where id = :cid"), {"cid": capability_id}
    )
    if capability_check.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Taxonomy entry not found")

    result = await session.execute(
        text("""
            insert into capability_taxonomy_keywords (capability_id, keyword, weight)
            values (:cid, :keyword, :weight)
            returning id
        """),
        {"cid": capability_id, "keyword": body.keyword.lower().strip(), "weight": body.weight},
    )
    new_id = result.scalar_one()
    await write_audit_log(
        session, user.tenant_id, user.sub, "taxonomy_keyword.added",
        "capability_taxonomy_keywords", str(new_id), None, body.model_dump(),
    )
    await session.commit()
    return {"id": str(new_id)}


@app.delete("/admin/taxonomy/keywords/{keyword_id}")
async def remove_taxonomy_keyword(
    keyword_id: str,
    user: TokenPayload = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("delete from capability_taxonomy_keywords where id = :kid returning capability_id"),
        {"kid": keyword_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keyword not found")

    await write_audit_log(
        session, user.tenant_id, user.sub, "taxonomy_keyword.removed",
        "capability_taxonomy_keywords", keyword_id, {"capability_id": str(row.capability_id)}, None,
    )
    await session.commit()
    return {"status": "removed"}

