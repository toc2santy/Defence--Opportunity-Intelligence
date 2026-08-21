"""
SAM.gov ingestion — network fetch + database orchestration.

HONESTY NOTE, kept in the code deliberately: the fetch_opportunities()
function below has NOT been exercised against the live SAM.gov API.
This sandbox has no network access and no SAM.gov API key. It was
written directly against GSA's own published documentation and
example responses (open.gsa.gov/api/get-opportunities-public-api),
and app/sam_gov_normalize.py IS tested against that exact documented
schema — but the live HTTP round-trip itself is unverified until
someone with a real key runs it. Treat the first real run as a test,
not a formality, the same way we treated the first `docker compose up`.
"""

import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.sam_gov_normalize import normalize_batch, DEFENSE_RELEVANT_NAICS, get_naics_group, NAICS_GROUP_SIZE
from app.ingestion_common import IngestionConfigError, get_rotation_index, advance_rotation_index  # noqa: F401 — re-exported for backward compatibility, existing imports elsewhere still work unchanged

SAM_GOV_API_KEY = os.environ.get("SAM_GOV_API_KEY")
SAM_GOV_BASE_URL = "https://api.sam.gov/opportunities/v2/search"
SAM_GOV_ROTATION_KEY = "SAM_GOV_NAICS_ROTATION"


async def fetch_opportunities(naics_code: str, posted_from: str, posted_to: str, limit: int = 100) -> dict:
    """
    One call = one NAICS code. SAM.gov personal API keys are rate
    limited to roughly 10 requests/day, so callers should keep the
    naics_codes list short per run rather than looping over dozens.
    Dates must be MM/dd/yyyy strings, and the SAM.gov API enforces
    a maximum 1-year range between them.
    """
    if not SAM_GOV_API_KEY:
        raise IngestionConfigError(
            "SAM_GOV_API_KEY is not set. Get a free key from your SAM.gov account's "
            "'Account Details' page, then set it as an environment variable."
        )

    params = {
        "api_key": SAM_GOV_API_KEY,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "ncode": naics_code,
        "limit": limit,
        "offset": 0,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(SAM_GOV_BASE_URL, params=params)
        response.raise_for_status()
        return response.json()


async def _get_or_create_organization(session: AsyncSession, name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    existing = await session.execute(
        text("select id from organizations where name = :name and org_type = 'government_body'"),
        {"name": name},
    )
    row = existing.first()
    if row:
        return str(row.id)

    result = await session.execute(
        text("""
            insert into organizations (name, org_type, country, classification)
            values (:name, 'government_body', 'United States', 'public')
            returning id
        """),
        {"name": name},
    )
    return str(result.scalar_one())


async def run_sam_gov_ingestion(
    session: AsyncSession,
    triggered_by_user_id: str,
    naics_codes: Optional[list[str]] = None,
    days_back: int = 90,
) -> dict:
    """
    Orchestrates: fetch (per NAICS code) -> normalize -> upsert into
    programmes (idempotent via the external_ref unique index from
    the migration) -> attach evidence -> record the run in
    ingestion_jobs. Writes to shared reference tables (programmes,
    organizations, evidence) — these are not tenant-scoped by
    design, same as the rest of the market/programme intelligence
    layer, so this data becomes visible to every tenant once ingested.

    If `naics_codes` is omitted, this now genuinely rotates through
    DEFENSE_RELEVANT_NAICS in groups, advancing a real persisted
    cursor after each successful run — fixing a real gap where the
    scheduled daily job always pulled the exact same first 3 codes
    forever, never actually widening coverage. Passing naics_codes
    explicitly (e.g. from an admin manually choosing codes) bypasses
    rotation entirely and doesn't disturb the cursor for next time.
    """
    used_rotation = naics_codes is None
    all_codes = list(DEFENSE_RELEVANT_NAICS.keys())
    if used_rotation:
        rotation_index = await get_rotation_index(session, SAM_GOV_ROTATION_KEY)
        naics_codes = get_naics_group(all_codes, rotation_index)

    source_result = await session.execute(
        text("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "SAM.gov source not seeded — run db/migrations/003_ingestion_sam_gov.sql first"
        )
    source_id = str(source_row.id)

    job_result = await session.execute(
        text("""
            insert into ingestion_jobs (source_id, status, started_at)
            values (:source_id, 'running', now())
            returning id
        """),
        {"source_id": source_id},
    )
    job_id = str(job_result.scalar_one())
    await session.commit()

    posted_to = datetime.utcnow()
    posted_from = posted_to - timedelta(days=days_back)
    posted_from_str = posted_from.strftime("%m/%d/%Y")
    posted_to_str = posted_to.strftime("%m/%d/%Y")

    total_ingested = 0
    all_failures = []
    errors = []

    try:
        for naics_code in naics_codes:
            try:
                raw_response = await fetch_opportunities(naics_code, posted_from_str, posted_to_str)
            except httpx.HTTPStatusError as e:
                errors.append(f"NAICS {naics_code}: HTTP {e.response.status_code} — {e.response.text[:200]}")
                continue
            except httpx.RequestError as e:
                errors.append(f"NAICS {naics_code}: request failed — {e}")
                continue

            raw_records = raw_response.get("opportunitiesData", [])
            normalized, failures = normalize_batch(raw_records)
            all_failures.extend(failures)

            for record in normalized:
                org_id = await _get_or_create_organization(session, record["organization_name"])

                upsert_result = await session.execute(
                    text("""
                        insert into programmes
                            (name, country, organization_id, stage, source_id, external_ref,
                             naics_code, response_deadline, set_aside_code, set_aside_description, last_updated)
                        values
                            (:name, :country, :organization_id, :stage, :source_id, :external_ref,
                             :naics_code, :response_deadline, :set_aside_code, :set_aside_description, now())
                        on conflict (source_id, external_ref) where external_ref is not null do update
                            set name = excluded.name,
                                organization_id = excluded.organization_id,
                                stage = excluded.stage,
                                naics_code = excluded.naics_code,
                                response_deadline = excluded.response_deadline,
                                set_aside_code = excluded.set_aside_code,
                                set_aside_description = excluded.set_aside_description,
                                last_updated = now()
                        returning id
                    """),
                    {
                        "name": record["name"], "country": record["country"],
                        "organization_id": org_id, "stage": record["stage"],
                        "source_id": source_id, "external_ref": record["external_ref"],
                        "naics_code": record["naics_code"],
                        "response_deadline": record["response_deadline"],
                        "set_aside_code": record["set_aside_code"],
                        "set_aside_description": record["set_aside_description"],
                    },
                )
                programme_id = str(upsert_result.scalar_one())

                claim = (
                    f"SAM.gov notice {record['external_ref']}, posted {record['posted_date']}, "
                    f"NAICS {record['naics_code']}, type: {record['raw_type']}"
                    + (f", set-aside: {record['set_aside_description']} ({record['set_aside_code']})"
                       if record['set_aside_code'] else "")
                )
                await session.execute(
                    text("""
                        insert into evidence
                            (source_id, related_entity_type, related_entity_id, claim, evidence_status, confidence)
                        values
                            (:source_id, 'programme', :programme_id, :claim, 'verified', 'high')
                    """),
                    {"source_id": source_id, "programme_id": programme_id, "claim": claim},
                )
                total_ingested += 1

        status = "succeeded" if not errors else ("failed" if total_ingested == 0 else "succeeded")
        await session.execute(
            text("""
                update ingestion_jobs
                set status = :status, finished_at = now(), records_ingested = :count,
                    error = :error
                where id = :job_id
            """),
            {
                "status": status, "count": total_ingested, "job_id": job_id,
                "error": "; ".join(errors) if errors else None,
            },
        )
        await session.commit()

    except Exception as e:
        await session.execute(
            text("update ingestion_jobs set status = 'failed', finished_at = now(), error = :error where id = :job_id"),
            {"error": str(e), "job_id": job_id},
        )
        await session.commit()
        raise

    if used_rotation:
        total_groups = max(1, (len(all_codes) + NAICS_GROUP_SIZE - 1) // NAICS_GROUP_SIZE)
        await advance_rotation_index(session, SAM_GOV_ROTATION_KEY, total_groups)

    return {
        "job_id": job_id,
        "records_ingested": total_ingested,
        "failures": all_failures,
        "errors": errors,
        "naics_codes_queried": naics_codes,
        "used_automatic_rotation": used_rotation,
        "date_range": {"from": posted_from_str, "to": posted_to_str},
    }
