"""
SECOP II (Colombia Compra Eficiente) ingestion — network fetch +
database orchestration.

Seventh real source, first in Latin America, and the first since
CanadaBuys whose classification codes feed capability matching
directly (UNSPSC — see app/colombia_normalize.py for why that
matters). It is also the third source able to populate
contract_awards, after EU TED and UK Find a Tender.

THE FETCH IS A SOCRATA QUERY, NOT A BULK DOWNLOAD. datos.gov.co
exposes this dataset through the Socrata Open Data API, which takes
SoQL parameters ($where/$order/$limit/$offset). That is a real
advantage over the CSV sources here: the buyer and date filters run
server-side, so a run transfers a few hundred relevant rows instead
of the whole national procurement history. It also means the query
itself is part of the contract — see build_where().

RATE LIMITING: anonymous requests are throttled and answer HTTP 429
under load (observed live). An app token raises the limit and is read
from SOCRATA_APP_TOKEN when set, but it is deliberately OPTIONAL —
this source must keep working with no credentials at all, like every
other source here except SAM.gov. A 429 is treated as a retryable
condition with backoff, not as a failure.
"""

import asyncio
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import httpx

from app.colombia_normalize import (
    COL_ENTITY,
    COL_PUBLISHED,
    DEFENCE_BUYER_PHRASES,
    normalize_batch,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

COLOMBIA_BASE_URL = "https://www.datos.gov.co/resource/p6dx-8zbt.json"
COLOMBIA_SOURCE_NAME = "SECOP II (Colombia Compra Eficiente)"

# Socrata's own maximum page size is 50,000, but a page that large
# takes long enough to be fragile; 1,000 keeps each request quick and
# the server-side filter means few pages are needed at all.
COLOMBIA_PAGE_SIZE = 1000
COLOMBIA_MAX_PAGES = 5
COLOMBIA_TIMEOUT_SECONDS = 60.0

# 429 is expected without an app token, so it gets real backoff rather
# than counting as a failed run.
COLOMBIA_MAX_RETRIES = 3
COLOMBIA_RETRY_BACKOFF_SECONDS = 5.0

COLOMBIA_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
}


def build_where(date_from: str) -> str:
    """
    The server-side filter. Buyer matching is duplicated here as SoQL
    `like` clauses purely to avoid transferring the whole country's
    procurement — app/colombia_normalize.is_defence_buyer remains the
    authority and re-checks every row that comes back, including the
    accent folding and the "DEFENSA does not mean defence" exclusions
    that SoQL cannot express.
    """
    buyers = " OR ".join(
        f"upper({COL_ENTITY}) like '%{phrase}%'" for phrase in DEFENCE_BUYER_PHRASES
    )
    return f"{COL_PUBLISHED} > '{date_from}T00:00:00' AND ({buyers})"


async def fetch_rows(
    date_from: str,
    max_pages: int = COLOMBIA_MAX_PAGES,
) -> tuple[list[dict], Optional[str]]:
    """
    Pages through the query with $offset. Same partial-failure
    contract as every other source: a failure on a later page stops
    paging but keeps whatever earlier pages already returned.
    """
    headers = dict(COLOMBIA_HEADERS)
    app_token = os.getenv("SOCRATA_APP_TOKEN", "").strip()
    if app_token:
        headers["X-App-Token"] = app_token

    rows: list[dict] = []
    where = build_where(date_from)

    async with httpx.AsyncClient(timeout=COLOMBIA_TIMEOUT_SECONDS, headers=headers) as client:
        for page in range(max_pages):
            params = {
                "$where": where,
                "$order": f"{COL_PUBLISHED} DESC",
                "$limit": COLOMBIA_PAGE_SIZE,
                "$offset": page * COLOMBIA_PAGE_SIZE,
            }
            page_rows, error = await _fetch_page(client, params)
            if error:
                return rows, error
            rows.extend(page_rows)
            if len(page_rows) < COLOMBIA_PAGE_SIZE:
                break  # last page
    return rows, None


async def _fetch_page(client: httpx.AsyncClient, params: dict) -> tuple[list[dict], Optional[str]]:
    for attempt in range(COLOMBIA_MAX_RETRIES):
        try:
            response = await client.get(COLOMBIA_BASE_URL, params=params)
            if response.status_code == 429:
                # Throttled, not broken. Back off and try again before
                # giving up — with no app token this is the normal way
                # a busy run gets slowed down.
                if attempt < COLOMBIA_MAX_RETRIES - 1:
                    await asyncio.sleep(COLOMBIA_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                return [], (
                    "rate limited by datos.gov.co (HTTP 429) after "
                    f"{COLOMBIA_MAX_RETRIES} attempts — set SOCRATA_APP_TOKEN to raise the limit"
                )
            response.raise_for_status()
            return response.json(), None
        except httpx.HTTPStatusError as e:
            return [], f"HTTP {e.response.status_code} — {e.response.text[:200]}"
        except httpx.RequestError as e:
            # httpx timeout exceptions can carry an empty str() — the
            # bug that made South Africa's outage unreadable for three
            # weeks (see south_africa_ingestion._request_error).
            detail = str(e).strip() or type(e).__name__
            return [], f"request failed — {detail}"
        except ValueError as e:
            return [], f"response was not JSON — {e}"
    return [], "exhausted retries"


async def run_colombia_ingestion(
    session: "AsyncSession",
    triggered_by_user_id: str,
    days_back: int = 30,
) -> dict:
    """
    Orchestrates: server-filtered query -> normalize + three-part
    relevance filter -> upsert programmes -> evidence -> award winners
    -> record the run. Same shape as every other source's run_*.
    """
    from sqlalchemy import text

    from app.ingestion_common import (
        IngestionConfigError,
        get_or_create_oem_organization,
        get_or_create_government_buyer,
        record_contract_award,
    )

    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": COLOMBIA_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "SECOP II Colombia source not seeded — run db/migrations/027_colombia_source.sql first"
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

    date_from = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    total_ingested = 0
    awards_recorded = 0
    all_failures: list[dict] = []
    errors: list[str] = []
    raw_rows: list[dict] = []

    try:
        raw_rows, fetch_error = await fetch_rows(date_from)
        if fetch_error:
            errors.append(fetch_error)

        normalized, failures = normalize_batch(raw_rows)
        all_failures.extend(failures)
        print(
            f"[colombia_ingestion] {len(raw_rows)} rows -> {len(normalized)} kept after "
            "buyer + support-unit + goods filters"
        )

        for record in normalized:
            org_id = await get_or_create_government_buyer(session, record["organization_name"], "Colombia")

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, ui_link,
                         naics_code, response_deadline, contact_address, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                         :classification_code, :response_deadline, :contact_address, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            ui_link = excluded.ui_link,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            naics_code = excluded.naics_code,
                            contact_address = excluded.contact_address,
                            last_updated = now()
                    returning id
                """),
                {
                    "name": record["name"], "country": record["country"],
                    "organization_id": org_id, "stage": record["stage"],
                    "source_id": source_id, "external_ref": record["external_ref"],
                    "ui_link": record["ui_link"],
                    "classification_code": record["classification_code"],
                    "response_deadline": record["response_deadline"],
                    "contact_address": record["contact_address"],
                },
            )
            programme_id = str(upsert_result.scalar_one())

            claim = (
                f"SECOP II procedure {record['external_ref']}, published {record['posted_date']}, "
                f"by defence buyer {record['organization_name'] or 'unknown'}"
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

            # Award winner. normalize_row already dropped the literal
            # "No Definido" the feed uses for an empty supplier, which
            # is the guard that matters here: adjudicado = 'Si' alone
            # is NOT evidence of a named winner (observed live on 14 of
            # 36 awarded rows).
            if record["winner_name"]:
                winner_org_id = await get_or_create_oem_organization(
                    session, record["winner_name"], "Colombia"
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
                                f"SECOP II procedure {record['external_ref']} names "
                                f"{record['winner_name']} as the awarded supplier"
                                + (
                                    f" (NIT {record['winner_identifier']})"
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
        "rows_examined": len(raw_rows),
        "failures": all_failures[:20],
        "errors": errors,
        "date_from": date_from,
    }
