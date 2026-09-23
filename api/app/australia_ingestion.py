"""
AusTender (Australia) ingestion — network fetch + database
orchestration.

Ninth real source. Unlike every other date-windowed source here, this
one paginates by CURSOR rather than by page number (response.links.next
— see the module docstring on why that's the same shape UK Find a
Tender and eTenders South Africa already use) inside one fixed
[dataInicial, dataFinal]-equivalent date window, so `days_back` bounds
how far back the window starts and MAX_PAGES bounds how many 100-row
cursor pages a single run will follow within it.
"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import httpx

from app.australia_normalize import normalize_batch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

AUSTRALIA_SOURCE_NAME = "AusTender (Australian Government)"
AUSTRALIA_BASE_URL = "https://api.tenders.gov.au/ocds/findByDates/contractPublished"

# Confirmed live: 100 releases per page, fixed, not configurable via
# a query parameter. A 100-day window already ran to several pages in
# live testing, so this is capped rather than followed to exhaustion —
# same reasoning as every other page/cursor-bounded source here.
AUSTRALIA_MAX_PAGES = 20
AUSTRALIA_TIMEOUT_SECONDS = 30.0

AUSTRALIA_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_releases(
    days_back: int = 90,
    max_pages: int = AUSTRALIA_MAX_PAGES,
) -> tuple[list[dict], Optional[str]]:
    """
    Follows response.links.next up to max_pages times. Same
    partial-failure contract as every other source: a failure on a
    later page stops paging but keeps whatever earlier pages already
    returned.
    """
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(days=days_back))
    end = _iso(now)
    url = f"{AUSTRALIA_BASE_URL}/{start}/{end}"

    releases: list[dict] = []
    error: Optional[str] = None

    async with httpx.AsyncClient(timeout=AUSTRALIA_TIMEOUT_SECONDS, headers=AUSTRALIA_HEADERS) as client:
        for _ in range(max_pages):
            try:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as e:
                error = f"HTTP {e.response.status_code} — {e.response.text[:200]}"
                break
            except httpx.RequestError as e:
                # httpx timeout exceptions can carry an empty str() —
                # the same bug class already fixed for South Africa
                # and CPPP (see their own modules).
                detail = str(e).strip() or type(e).__name__
                error = f"request failed — {detail}"
                break
            except ValueError as e:
                error = f"response was not JSON — {e}"
                break

            releases.extend(payload.get("releases") or [])
            next_url = (payload.get("links") or {}).get("next")
            if not next_url:
                break
            url = next_url

    return releases, error


async def run_australia_ingestion(
    session: "AsyncSession",
    triggered_by_user_id: str,
    days_back: int = 90,
) -> dict:
    """
    Orchestrates: cursor-paginated fetch -> normalize + defence-buyer
    filter -> upsert programmes (always stage='contract_awarded',
    this feed being contract notices only) -> evidence -> award
    winner -> record the run. Same shape as every other source's run_*.
    """
    from sqlalchemy import text

    from app.ingestion_common import (
        IngestionConfigError,
        get_or_create_oem_organization,
        get_or_create_government_buyer,
        record_contract_award,
    )

    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": AUSTRALIA_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "AusTender source not seeded — run db/migrations/038_australia_source.sql first"
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

    total_ingested = 0
    awards_recorded = 0
    all_failures: list[dict] = []
    errors: list[str] = []
    raw_releases: list[dict] = []

    try:
        raw_releases, fetch_error = await fetch_releases(days_back=days_back)
        if fetch_error:
            errors.append(fetch_error)

        normalized, failures = normalize_batch(raw_releases)
        all_failures.extend(failures)
        print(
            f"[australia_ingestion] {len(raw_releases)} releases -> {len(normalized)} kept "
            "after the Department of Defence buyer filter"
        )

        for record in normalized:
            org_id = await get_or_create_government_buyer(
                session, record["organization_name"], "Australia"
            )

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, ui_link,
                         naics_code, last_updated)
                    values
                        (:name, :country, :organization_id, 'contract_awarded', :source_id,
                         :external_ref, :ui_link, :classification_code, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            organization_id = excluded.organization_id,
                            naics_code = excluded.naics_code,
                            last_updated = now()
                    returning id
                """),
                {
                    "name": record["name"], "country": record["country"],
                    "organization_id": org_id, "source_id": source_id,
                    "external_ref": record["external_ref"], "ui_link": record["ui_link"],
                    "classification_code": record["classification_code"],
                },
            )
            programme_id = str(upsert_result.scalar_one())

            claim = (
                f"AusTender contract notice {record['external_ref']}, "
                f"buyer {record['organization_name'] or 'unknown'}"
                + (
                    f", UNSPSC {record['classification_code']}"
                    if record["classification_code"]
                    else ", no usable UNSPSC code published"
                )
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

            if record["winner_name"]:
                winner_org_id = await get_or_create_oem_organization(
                    session, record["winner_name"], "Australia"
                )
                if winner_org_id is not None:
                    await record_contract_award(
                        session, programme_id, winner_org_id, source_id,
                        value_amount=record["value_amount"], value_currency=record["value_currency"],
                    )
                    awards_recorded += 1
                    await session.execute(
                        text("""
                            insert into evidence
                                (source_id, related_entity_type, related_entity_id, claim,
                                 evidence_status, confidence)
                            values
                                (:source_id, 'organization', :org_id, :claim, 'verified', 'high')
                        """),
                        {
                            "source_id": source_id,
                            "org_id": winner_org_id,
                            "claim": (
                                f"AusTender contract {record['external_ref']} names "
                                f"{record['winner_name']} as the awarded supplier"
                                + (
                                    f" (ABN {record['winner_identifier']})"
                                    if record["winner_identifier"]
                                    else ""
                                )
                            ),
                        },
                    )

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
            text("""
                update ingestion_jobs
                set status = 'failed', finished_at = now(), error = :error
                where id = :job_id
            """),
            {"error": str(e)[:500], "job_id": job_id},
        )
        await session.commit()
        raise

    return {
        "job_id": job_id,
        "records_ingested": total_ingested,
        "awards_recorded": awards_recorded,
        "releases_examined": len(raw_releases),
        "failures": all_failures[:20],
        "errors": errors,
    }
