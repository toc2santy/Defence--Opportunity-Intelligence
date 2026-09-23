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
from app.ingestion_common import (
    IngestionConfigError, get_or_create_oem_organization, get_or_create_government_buyer, record_contract_award,
)

TED_API_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_SOURCE_NAME = "EU TED (Tenders Electronic Daily)"

# Fields to request from the API — only what we actually store,
# not the full ~1300 available fields.
TED_FIELDS = [
    "publication-number", "notice-title", "buyer-name",
    "buyer-country", "classification-cpv", "publication-date",
    "notice-type", "deadline-date-lot", "deadline-receipt-request",
    # OEM Intelligence — winner-name/winner-country are only present
    # on award-stage notices; confirmed valid TED v3 field names live.
    "winner-name", "winner-country",
    # Procurement contact — found live (2026-09) after a user report
    # that Tender Briefing showed no contact for TED-sourced tenders.
    # Confirmed against 5 real notices (one a Ministerie van
    # Defensie/Netherlands MoD tender): buyer-email and the three
    # address parts are consistently populated, not sparse — a real,
    # previously-uncaptured gap on this platform's single biggest
    # source, not a field that happens to be usually empty.
    "buyer-email", "buyer-post-code", "buyer-city", "organisation-street-buyer",
    # Eligibility/Backup Phase 1 (2026-09) — TED's own real bidder-
    # restriction fields, confirmed live against real defence-
    # relevant notices (see ted_eu_normalize.py's own comment on the
    # verification). The first source beyond SAM.gov's set-aside
    # confirmed to publish a genuine, structured restriction signal.
    "reserved-procurement-lot", "sme-lot",
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


# TED's own hard cap on the `limit` parameter — confirmed live
# (limit=500 is rejected with SEARCH_EXCEEDS_MAX_LIMIT, maxLimits=250).
TED_MAX_PAGE_SIZE = 250

# Safety cap on total notices pulled per ingestion run, across all
# pages — the defense CPV filter alone can match thousands of
# notices per month once TED's full archive is in scope, and this
# keeps one run bounded and fast rather than looping until
# exhausted. Raise if a wider single-run pull is ever needed.
TED_MAX_NOTICES_PER_RUN = 1000


async def fetch_notices(query: str, limit: int = TED_MAX_PAGE_SIZE, page: int = 1) -> dict:
    """
    No API key required — confirmed from official TED docs.

    scope=ALL (the full historical archive, as opposed to ACTIVE)
    paginates via a plain `page` number, not `iterationNextToken` —
    confirmed live: iterationNextToken came back null on every page
    under scope=ALL regardless of how many more results existed,
    while incrementing `page` correctly walked through the full
    result set and returned an empty `notices` list (HTTP 200, not
    an error) once past the end.
    """
    payload = {
        "query": query,
        "fields": TED_FIELDS,
        "limit": limit,
        "page": page,
        "scope": "ALL",
        "checkQuerySyntax": False,
    }
    print(f"[ted_eu_ingestion] sending query (page {page}): {query}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(TED_API_URL, json=payload)
        response.raise_for_status()
        return response.json()


async def fetch_all_notices(
    query: str, max_notices: int = TED_MAX_NOTICES_PER_RUN
) -> tuple[list[dict], Optional[str]]:
    """
    Walks pages of TED_MAX_PAGE_SIZE until either a short/empty page
    signals the end of the result set, or max_notices is reached.

    A failure on any page after the first stops pagination but keeps
    whatever notices earlier pages already returned, rather than
    discarding a partially-successful run — the error is returned
    alongside them instead of raised, matching how a first-page
    failure was already handled by the caller before pagination
    existed.
    """
    all_notices: list[dict] = []
    page = 1
    while len(all_notices) < max_notices:
        try:
            body = await fetch_notices(query, limit=TED_MAX_PAGE_SIZE, page=page)
        except httpx.HTTPStatusError as e:
            return all_notices, f"HTTP {e.response.status_code} — {e.response.text[:200]}"
        except httpx.RequestError as e:
            return all_notices, f"request failed — {e}"

        page_notices = body.get("notices", [])
        all_notices.extend(page_notices)
        if len(page_notices) < TED_MAX_PAGE_SIZE:
            break  # short page — this was the last one
        page += 1
    return all_notices[:max_notices], None


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

        raw_notices, fetch_error = await fetch_all_notices(query)
        if fetch_error:
            errors.append(fetch_error)
        # Debug: log what TED actually returned so we can diagnose
        # zero-result issues without needing to inspect raw HTTP
        print(f"[ted_eu_ingestion] notices count: {len(raw_notices)}")
        normalized, failures = normalize_batch(raw_notices)
        all_failures.extend(failures)

        for record in normalized:
            org_id = await get_or_create_government_buyer(session, record["organization_name"], record["country"])

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, ui_link,
                         naics_code, response_deadline, contact_email, contact_address,
                         set_aside_code, set_aside_description, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                         :classification_code, :response_deadline, :contact_email, :contact_address,
                         :set_aside_code, :set_aside_description, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            ui_link = excluded.ui_link,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            naics_code = excluded.naics_code,
                            response_deadline = excluded.response_deadline,
                            contact_email = excluded.contact_email,
                            contact_address = excluded.contact_address,
                            set_aside_code = excluded.set_aside_code,
                            set_aside_description = excluded.set_aside_description,
                            last_updated = now()
                    returning id
                """),
                {
                    "name": record["name"], "country": record["country"],
                    "organization_id": org_id, "stage": record["stage"],
                    "source_id": source_id, "external_ref": record["external_ref"],
                    "ui_link": record.get("ui_link"),
                    "classification_code": record["classification_code"],
                    "response_deadline": record["response_deadline"],
                    "contact_email": record["contact_email"],
                    "contact_address": record["contact_address"],
                    "set_aside_code": record["set_aside_code"],
                    "set_aside_description": record["set_aside_description"],
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

            # OEM Intelligence — only populated on award-stage notices
            # (extract_winners returns [] otherwise), so this is a
            # no-op for every non-award notice this source ingests.
            for winner in record["winners"]:
                winner_org_id = await get_or_create_oem_organization(
                    session, winner["name"], winner["country"]
                )
                if winner_org_id is None:
                    continue
                award_id = await record_contract_award(session, programme_id, winner_org_id, source_id)
                await session.execute(
                    text("""
                        insert into evidence
                            (source_id, related_entity_type, related_entity_id, claim, evidence_status, confidence)
                        values
                            (:source_id, 'contract_award', :award_id, :claim, 'verified', 'high')
                    """),
                    {
                        "source_id": source_id, "award_id": award_id,
                        "claim": (
                            f"TED notice {record['external_ref']} names {winner['name']} "
                            f"as winner (country: {winner['country'] or 'not specified'})"
                        ),
                    },
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
