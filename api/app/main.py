"""
Defence Opportunity Intelligence — API foundation.

Phase 0, revised after first live run: reads real config from the
environment (was hardcoded before), and adds the signup/login
routes that were missing — without these, there was no way to
create a tenant/user or obtain a real token to test anything
beyond /healthz.
"""

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
from app.sam_gov_normalize import DEFENSE_RELEVANT_NAICS, get_naics_group, NAICS_GROUP_SIZE
from app.ingestion_common import IngestionSourceConfig, run_scheduled_source
from app.programme_matching import match_product_to_programmes
from app.credential_status import compute_status

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
]


@app.on_event("startup")
async def start_scheduler():
    for source in INGESTION_SOURCES:
        scheduler.add_job(
            run_scheduled_source,
            "interval",
            hours=source.interval_hours,
            id=source.scheduler_job_id,
            replace_existing=True,
            kwargs={"source_config": source, "session_factory": SessionLocal},
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


def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str) -> TokenPayload:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return TokenPayload(**data)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenPayload:
    return decode_token(creds.credentials)


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
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


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
            text("insert into tenants (name) values (:name) returning id"),
            {"name": body.company_name},
        )
        tenant_id = tenant_result.scalar_one()

        admin_role = await session.execute(text("select id from roles where name = 'admin'"))
        admin_role_id = admin_role.scalar_one()

        user_result = await session.execute(
            text("""
                insert into users (tenant_id, email, password_hash, role_id)
                values (:tenant_id, :email, :password_hash, :role_id)
                returning id
            """),
            {
                "tenant_id": str(tenant_id), "email": body.email,
                "password_hash": password_hash, "role_id": str(admin_role_id),
            },
        )
        user_id = user_result.scalar_one()

        await session.execute(
            text("select set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        await write_audit_log(
            session, str(tenant_id), str(user_id), "tenant.created_via_signup",
            "tenant", str(tenant_id), None, {"company_name": body.company_name},
        )
        await session.commit()

    token = create_access_token(str(user_id), str(tenant_id), "admin")
    return TokenOut(access_token=token)


@app.post("/auth/login", response_model=TokenOut)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(request: Request, body: LoginIn):
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                select u.id, u.tenant_id, u.password_hash, r.name as role_name
                from users u join roles r on r.id = u.role_id
                where u.email = :email and u.is_active = true
            """),
            {"email": body.email},
        )
        row = result.first()

    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    try:
        ph.verify(row.password_hash, body.password)
    except VerifyMismatchError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    token = create_access_token(str(row.id), str(row.tenant_id), row.role_name)
    return TokenOut(access_token=token)


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
            select u.email, r.name as role_name, t.name as company_name, t.plan, u.is_platform_admin
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
        "company_name": row.company_name,
        "plan": row.plan,
        "is_platform_admin": row.is_platform_admin,
    }


# ---------------------------------------------------------------
# Example tenant-scoped routes — unchanged in logic, now actually
# reachable since doi_app has real grants and signup/login exist.
# ---------------------------------------------------------------
class ProductIn(BaseModel):
    name: str
    description: Optional[str] = None
    trl: Optional[int] = None


@app.post("/products", status_code=201)
async def create_product(
    body: ProductIn,
    user: TokenPayload = Depends(require_role("admin", "analyst")),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            insert into products (tenant_id, name, description, trl, created_by)
            values (:tenant_id, :name, :description, :trl, :created_by)
            returning id
        """),
        {
            "tenant_id": user.tenant_id, "name": body.name,
            "description": body.description, "trl": body.trl, "created_by": user.sub,
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
    result = await session.execute(text("select id, name, trl, created_at from products"))
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
):
    """
    The genuinely generalized view — every registered source, not
    just SAM.gov. Right now that's still a list of one, honestly,
    since UK Contracts Finder (the next planned source) isn't built
    yet. Adding it means this list grows to two entries with no
    other change required here — that's the actual proof this
    registry pattern works, not a promise about it.
    """
    sources_status = []
    for source in INGESTION_SOURCES:
        job = scheduler.get_job(source.scheduler_job_id)
        sources_status.append({
            "code": source.code,
            "display_name": source.display_name,
            "interval_hours": source.interval_hours,
            "api_key_configured": bool(os.environ.get(source.api_key_env_var)) if source.api_key_env_var else True,
            "scheduled": job is not None,
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
        })
    return {"sources": sources_status}


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

    matches = await match_product_to_programmes(session, product_id)

    persisted = []
    for m in matches:
        upsert_result = await session.execute(
            text("""
                insert into opportunities
                    (tenant_id, product_id, programme_id, organization_id, score, confidence, stage)
                values
                    (:tenant_id, :product_id, :programme_id, :organization_id, :score, :confidence, 'lead')
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
            },
        )
        row = upsert_result.first()
        persisted.append({**m, "opportunity_id": str(row.id), "stage": row.stage})

    await write_audit_log(
        session, user.tenant_id, user.sub, "product.matched_programmes",
        "product", product_id, None, {"match_count": len(persisted)},
    )
    await session.commit()
    return {"product_id": product_id, "matches": persisted}


@app.get("/opportunities")
async def list_opportunities(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
):
    result = await session.execute(
        text("""
            select o.id, o.score, o.confidence, o.stage, o.next_action, o.created_at,
                   p.name as product_name, pr.name as programme_name, org.name as organization_name,
                   pr.set_aside_code, pr.set_aside_description, pr.response_deadline
            from opportunities o
            join products p on p.id = o.product_id
            left join programmes pr on pr.id = o.programme_id
            left join organizations org on org.id = o.organization_id
            order by o.score desc nulls last
        """)
    )
    return [dict(row._mapping) for row in result]


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
    return [dict(row._mapping) for row in result]


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

