"""
UK Find a Tender ingestion — network fetch + database orchestration.

HONESTY NOTE, same discipline as sam_gov_ingestion.py: fetch_releases()
has NOT been exercised against the live API from this sandbox (no
network access here). It was written directly against GOV.UK's own
published documentation and example response, and
app/uk_ft_normalize.py IS tested against that exact documented
schema — but the live round-trip is unverified until someone with
network access runs it.

REAL DIFFERENCE FROM SAM.GOV: no API key needed at all (confirmed —
the official docs show no auth header anywhere for this endpoint),
and no NAICS-style rotation, because this API has no server-side
category filter to rotate through — one date-range call naturally
covers every category, filtered for defense-relevance client-side
in app/uk_ft_normalize.py.
"""

from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.uk_ft_normalize import normalize_batch
from app.ingestion_common import IngestionConfigError

UK_FT_BASE_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
UK_FT_SOURCE_NAME = "UK Find a Tender Service"


async def fetch_releases(
    updated_from: str, updated_to: str, stages: str = "planning,tender,award", limit: int = 100
) -> dict:
    """
    No API key required — confirmed from the official GOV.UK API
    docs, which show no auth header anywhere for this read endpoint.
    Dates must be YYYY-MM-DDTHH:MM:SS. The API enforces its own
    dynamic rate limit — real 429 responses include a Retry-After
    header, which this function surfaces rather than silently
    retrying, since retry timing should be a caller/scheduler
    decision, not hidden inside the fetch itself.
    """
    params = {
        "updatedFrom": updated_from,
        "updatedTo": updated_to,
        "stages": stages,
        "limit": limit,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(UK_FT_BASE_URL, params=params)
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
            values (:name, 'government_body', 'United Kingdom', 'public')
            returning id
        """),
        {"name": name},
    )
    return str(result.scalar_one())


async def run_uk_ft_ingestion(
    session: AsyncSession,
    triggered_by_user_id: str,
    days_back: int = 7,
    stages: str = "planning,tender,award",
) -> dict:
    """
    Orchestrates: fetch (one date-range call, no per-category
    looping — see module docstring) -> normalize + filter for
    defense-relevance -> upsert into programmes (idempotent, same
    external_ref unique index SAM.gov ingestion uses) -> attach
    evidence -> record the run in ingestion_jobs. Writes to the same
    shared reference tables as every other source — every tenant
    benefits from this data once ingested, same as SAM.gov's.

    days_back defaults to 7, not SAM.gov's 90 — this API is queried
    by *updated* date, not posted date, and is meant to be polled
    frequently for genuinely new/changed notices, not swept broadly
    the way a rotation-based, rate-limited API needs to be.
    """
    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": UK_FT_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "UK Find a Tender source not seeded — run db/migrations/009_uk_find_a_tender.sql first"
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

    updated_to = datetime.utcnow()
    updated_from = updated_to - timedelta(days=days_back)
    updated_from_str = updated_from.strftime("%Y-%m-%dT%H:%M:%S")
    updated_to_str = updated_to.strftime("%Y-%m-%dT%H:%M:%S")

    total_ingested = 0
    all_failures = []
    errors = []

    try:
        try:
            raw_response = await fetch_releases(updated_from_str, updated_to_str, stages)
        except httpx.HTTPStatusError as e:
            retry_after = e.response.headers.get("Retry-After")
            errors.append(
                f"HTTP {e.response.status_code} — {e.response.text[:200]}"
                + (f" (Retry-After: {retry_after}s)" if retry_after else "")
            )
            raw_response = {"releases": []}
        except httpx.RequestError as e:
            errors.append(f"request failed — {e}")
            raw_response = {"releases": []}

        raw_releases = raw_response.get("releases", [])
        normalized, failures = normalize_batch(raw_releases)
        all_failures.extend(failures)

        for record in normalized:
            org_id = await _get_or_create_organization(session, record["organization_name"])

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, naics_code, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :classification_code, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            last_updated = now()
                    returning id
                """),
                {
                    "name": record["name"], "country": record["country"],
                    "organization_id": org_id, "stage": record["stage"],
                    "source_id": source_id, "external_ref": record["external_ref"],
                    "classification_code": record["classification_code"],
                },
            )
            programme_id = str(upsert_result.scalar_one())

            claim = (
                f"UK Find a Tender notice {record['external_ref']}, updated {record['posted_date']}, "
                f"CPV {record['classification_code']}, stage: {record['stage']}"
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
                set status = :status, finished_at = now(), records_ingested = :count, error = :error
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

    return {
        "job_id": job_id,
        "records_ingested": total_ingested,
        "failures": all_failures,
        "errors": errors,
        "date_range": {"from": updated_from_str, "to": updated_to_str},
    }
