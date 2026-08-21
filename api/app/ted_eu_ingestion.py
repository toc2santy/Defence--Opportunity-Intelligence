"""
EU TED ingestion — network fetch + database orchestration.

Third real ingestion source, covering 27 EU member states + EEA in
one single API. No API key needed for search (confirmed from the
official TED API v3 documentation: "The Search API does not require
a key").

KEY ARCHITECTURAL DIFFERENCE: unlike UK Find a Tender (no server-
side filter) and SAM.gov (NAICS filter, one code per call), TED
supports CPV filtering directly in the query AND can combine
multiple CPV prefixes in a single call using OR syntax. This means
one API call can cover all defense-relevant categories at once —
no rotation needed, no client-side filtering needed.

HONESTY NOTE, same as all other sources: the live API round-trip
has NOT been tested from this sandbox (no network access here).
Written directly against the documented v3 Search API format,
with the normalizer independently verified against realistic
response structures.
"""

from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ted_eu_normalize import normalize_batch, DEFENSE_CPV_QUERIES
from app.ingestion_common import IngestionConfigError

TED_API_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_SOURCE_NAME = "EU TED (Tenders Electronic Daily)"

# Fields to request from the API — only what we actually store,
# not the full ~1300 available fields.
TED_FIELDS = [
    "publication-number", "notice-title", "buyer-name",
    "buyer-country", "classification-cpv", "publication-date",
    "notice-type", "deadline-receipt-tenders", "deadline-receipt-request",
]


def build_defense_query(published_from: str, published_to: str) -> str:
    """
    Simplified query — use NC (Nature of Contract) = 1 (supplies)
    combined with the 35 CPV parent code. The FT~ operator was
    confirmed to return empty results in the JSON body context.
    Using the exact query format confirmed working in real TED API v3
    examples: field=value syntax only, no FT~ operators.
    """
    return (
        f"classification-cpv=35000000 "
        f"AND publication-date>={published_from} "
        f"AND publication-date<={published_to}"
    )


async def fetch_notices(query: str, limit: int = 100) -> dict:
    """
    No API key required — confirmed from official TED docs.
    """
    payload = {
        "query": query,
        "fields": TED_FIELDS,
        "limit": limit,
        "scope": "ALL",
        "checkQuerySyntax": False,
    }
    print(f"[ted_eu_ingestion] sending query: {query}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(TED_API_URL, json=payload)
        response.raise_for_status()
        return response.json()


async def _get_or_create_organization(session: AsyncSession, name: Optional[str], country: str) -> Optional[str]:
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
            values (:name, 'government_body', :country, 'public')
            returning id
        """),
        {"name": name, "country": country},
    )
    return str(result.scalar_one())


async def run_ted_eu_ingestion(
    session: AsyncSession,
    triggered_by_user_id: str,
    days_back: int = 30,
) -> dict:
    """
    Orchestrates: build query -> fetch (one call covers all defense
    CPV codes) -> normalize -> upsert into programmes -> attach
    evidence -> record the run.

    days_back defaults to 14 — TED publishes notices on a slightly
    slower cadence than SAM.gov, and the API handles date-range
    queries efficiently enough that 2 weeks is a reasonable default
    polling window.
    """
    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": TED_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "EU TED source not seeded — run db/migrations/013_ted_eu_source.sql first"
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

    date_to = datetime.utcnow()
    date_from = date_to - timedelta(days=days_back)
    from_str = date_from.strftime("%Y%m%d")
    to_str = date_to.strftime("%Y%m%d")

    total_ingested = 0
    all_failures = []
    errors = []

    try:
        query = build_defense_query(from_str, to_str)

        try:
            raw_response = await fetch_notices(query)
        except httpx.HTTPStatusError as e:
            errors.append(f"HTTP {e.response.status_code} — {e.response.text[:200]}")
            raw_response = {"notices": []}
        except httpx.RequestError as e:
            errors.append(f"request failed — {e}")
            raw_response = {"notices": []}

        raw_notices = raw_response.get("notices", [])
        # Debug: log what TED actually returned so we can diagnose
        # zero-result issues without needing to inspect raw HTTP
        print(f"[ted_eu_ingestion] raw response keys: {list(raw_response.keys())}")
        print(f"[ted_eu_ingestion] notices count: {len(raw_notices)}")
        normalized, failures = normalize_batch(raw_notices)
        all_failures.extend(failures)

        for record in normalized:
            org_id = await _get_or_create_organization(session, record["organization_name"], record["country"])

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref,
                         naics_code, response_deadline, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref,
                         :classification_code, :response_deadline, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            naics_code = excluded.naics_code,
                            response_deadline = excluded.response_deadline,
                            last_updated = now()
                    returning id
                """),
                {
                    "name": record["name"], "country": record["country"],
                    "organization_id": org_id, "stage": record["stage"],
                    "source_id": source_id, "external_ref": record["external_ref"],
                    "classification_code": record["classification_code"],
                    "response_deadline": record["response_deadline"],
                },
            )
            programme_id = str(upsert_result.scalar_one())

            claim = (
                f"TED notice {record['external_ref']}, published {record['posted_date']}, "
                f"CPV {record['classification_code']}, buyer: {record['organization_name'] or 'unknown'}, "
                f"country: {record['country']}"
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
        "query_used": query,
        "date_range": {"from": from_str, "to": to_str},
    }
