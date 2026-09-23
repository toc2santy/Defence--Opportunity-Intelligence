"""
eTenders South Africa (National Treasury) ingestion — network fetch +
database orchestration.

Sixth real ingestion source, and the first covering Africa. Verified
live before building (see db/migrations/020_south_africa_source.sql
for the Middle East sources assessed and rejected alongside it):
ocds-api.etenders.gov.za is a genuine open government JSON API, no
key required, same OCDS release shape UK Find a Tender uses —
including the same links.next cursor pagination.

REAL DIFFERENCE FROM UK FIND A TENDER: this feed carries no
CPV/NAICS/UNSPSC-equivalent classification code at all (tender.category
is free text), so defence-relevance is decided by the procuring
entity/buyer name instead — the same approach app/cppp_india_ingestion.py
uses for India, and matched rows carry the analogous ZA-DEF sentinel
(app/south_africa_normalize.py).

The API requires dateFrom/dateTo — there is no "give me everything"
call — so, like UK Find a Tender, this polls a recent date window
rather than sweeping a full history in one run.
"""

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from app.south_africa_normalize import normalize_batch

SOUTH_AFRICA_BASE_URL = "https://ocds-api.etenders.gov.za/api/OCDSReleases"
SOUTH_AFRICA_SOURCE_NAME = "eTenders South Africa (National Treasury)"

# Safety cap on total releases pulled per ingestion run, across all
# pages — the same guard TED and UK Find a Tender use.
SOUTH_AFRICA_MAX_RELEASES_PER_RUN = 1000

# ALL THREE NUMBERS BELOW WERE MEASURED AGAINST THE LIVE API, not
# guessed, after every run since 2026-08-24 returned zero records:
#
#   PageSize=200, timeout 45s  → ReadTimeout (the original settings)
#   PageSize=200, timeout 120s → ReadTimeout, still nothing at 130s
#   PageSize=50,  timeout 120s → 200 OK, 35 releases, 59.4s
#   page 2 (same size)         → 200 OK, 15 releases, 64.9s
#
# So this API's response time scales with page size badly enough that
# 200 simply never returns, while 50 returns in about a minute. The
# timeout has to sit well above that minute, and the page size well
# below the 200 that broke it.
SOUTH_AFRICA_PAGE_SIZE = 50
SOUTH_AFRICA_TIMEOUT_SECONDS = 120.0

# At ~60s per page, an unbounded crawl would hold an HTTP request open
# for many minutes. Four pages covers the whole 30-day window with
# room to spare — live sampling returned 50 releases in total for a
# 30-day window before the feed ran out.
SOUTH_AFRICA_MAX_PAGES = 4

# Sent because a plain identifying UA is good practice for a public
# API and costs nothing; no 403 was observed without it during
# verification, unlike CanadaBuys's CDN.
SOUTH_AFRICA_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
}


def _request_error(e: httpx.RequestError) -> str:
    """
    httpx timeout errors can carry an EMPTY str(), which is how this
    source spent weeks reporting `request failed — ` with nothing
    after the dash and no way to tell a timeout from a DNS failure.
    The exception class is always informative, so it is used whenever
    the message is not.
    """
    detail = str(e).strip() or type(e).__name__
    return f"request failed — {detail}"


def _decode(response: httpx.Response) -> dict:
    """
    This API can answer 200 with a body that is not JSON at all —
    observed live on page 3 of a 30-day window, after two good pages.
    `response.json()` raises json.JSONDecodeError there, which is
    neither an HTTPStatusError nor a RequestError, so it escaped both
    handlers and took the whole run down with a 500 instead of being
    recorded as the partial failure it is.
    """
    try:
        return response.json()
    except json.JSONDecodeError:
        raise ValueError(
            f"HTTP {response.status_code} but the body was not JSON "
            f"({len(response.content)} bytes, starts: {response.text[:80]!r})"
        )


async def fetch_all_releases(
    date_from: str,
    date_to: str,
    max_releases: int = SOUTH_AFRICA_MAX_RELEASES_PER_RUN,
) -> tuple[list[dict], Optional[str]]:
    """
    Follows the API's own links.next cursor URL — same pagination
    contract as UK Find a Tender's fetch_all_releases, including the
    same partial-failure handling: a failure on any page after the
    first stops pagination but keeps whatever earlier pages already
    returned.
    """
    all_releases: list[dict] = []
    async with httpx.AsyncClient(timeout=SOUTH_AFRICA_TIMEOUT_SECONDS, headers=SOUTH_AFRICA_HEADERS) as client:
        try:
            response = await client.get(
                SOUTH_AFRICA_BASE_URL,
                params={
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "PageSize": SOUTH_AFRICA_PAGE_SIZE,
                },
            )
            response.raise_for_status()
            body = _decode(response)
        except httpx.HTTPStatusError as e:
            return [], f"HTTP {e.response.status_code} — {e.response.text[:200]}"
        except httpx.RequestError as e:
            return [], _request_error(e)
        except ValueError as e:
            return [], str(e)

        pages = 1
        while True:
            page_releases = body.get("releases", [])
            all_releases.extend(page_releases)
            next_url = body.get("links", {}).get("next")
            if (
                not next_url
                or not page_releases
                or len(all_releases) >= max_releases
                or pages >= SOUTH_AFRICA_MAX_PAGES
            ):
                break
            try:
                response = await client.get(next_url)
                response.raise_for_status()
                body = _decode(response)
                pages += 1
            except httpx.HTTPStatusError as e:
                return all_releases[:max_releases], f"HTTP {e.response.status_code} — {e.response.text[:200]}"
            except httpx.RequestError as e:
                return all_releases[:max_releases], _request_error(e)
            except ValueError as e:
                # Partial-failure contract, same as every other source:
                # a broken later page keeps the pages already fetched.
                return all_releases[:max_releases], str(e)

    return all_releases[:max_releases], None


async def run_south_africa_ingestion(
    session: "AsyncSession",
    triggered_by_user_id: str,
    days_back: int = 30,
) -> dict:
    """
    Orchestrates: fetch a recent date-range window -> normalize +
    filter for defence-relevance -> upsert into programmes -> attach
    evidence -> record the run. Same shape as every other source's
    run_* function.

    days_back defaults to 30, not UK Find a Tender's 7 — ARMSCOR and
    the other South African defence buyers post far less often than
    UK/EU volumes (roughly 1-2 defence-relevant releases per ~100
    general releases in live sampling), so a week-wide window would
    frequently return nothing to ingest even when the source is
    working correctly.
    """
    # Imported here rather than at module level so the network/
    # parsing half of this file stays importable without sqlalchemy
    # in the local dev venv — the same arrangement
    # app/capability_resolver.py documents.
    from sqlalchemy import text

    from app.ingestion_common import IngestionConfigError, get_or_create_government_buyer

    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": SOUTH_AFRICA_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "eTenders South Africa source not seeded — run db/migrations/020_south_africa_source.sql first"
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
    date_from_str = date_from.strftime("%Y-%m-%d")
    date_to_str = date_to.strftime("%Y-%m-%d")

    total_ingested = 0
    all_failures = []
    errors = []
    raw_releases = []

    try:
        raw_releases, fetch_error = await fetch_all_releases(date_from_str, date_to_str)
        if fetch_error:
            errors.append(fetch_error)

        normalized, failures = normalize_batch(raw_releases)
        all_failures.extend(failures)
        print(
            f"[south_africa_ingestion] {len(raw_releases)} releases -> "
            f"{len(normalized)} defence-relevant after buyer + facilities-works filters"
        )

        for record in normalized:
            org_id = await get_or_create_government_buyer(session, record["organization_name"], "South Africa")

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, ui_link,
                         naics_code, response_deadline,
                         contact_name, contact_email, contact_phone, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                         :classification_code, :response_deadline,
                         :contact_name, :contact_email, :contact_phone, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            ui_link = excluded.ui_link,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            response_deadline = excluded.response_deadline,
                            contact_name = excluded.contact_name,
                            contact_email = excluded.contact_email,
                            contact_phone = excluded.contact_phone,
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
                    "contact_name": record.get("contact_name"),
                    "contact_email": record.get("contact_email"),
                    "contact_phone": record.get("contact_phone"),
                },
            )
            programme_id = str(upsert_result.scalar_one())

            claim = (
                f"eTenders South Africa notice {record['external_ref']}, published {record['posted_date']}, "
                f"closing {record['response_deadline']}, "
                f"published by defence-relevant buyer: {record['organization_name'] or 'unknown'}"
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
        "rows_examined": len(raw_releases),
        "failures": all_failures,
        "errors": errors,
        "date_range": {"from": date_from_str, "to": date_to_str},
    }
