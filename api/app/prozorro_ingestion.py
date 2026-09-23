"""
ProZorro (Ukraine) ingestion — network fetch + database orchestration.

Eighth real source, first in Eastern Europe, and architecturally the
most different from the other seven: ProZorro's public sync API
supports no server-side filter at all (verified live — see
app/prozorro_normalize.py's module docstring), so this is the only
source in this project built as a genuine TWO-PHASE fetch:

  Phase 1 — LIST SCAN. Walks the raw changes feed newest-first via its
  own `next_page` cursor, asking only for the handful of fields that
  endpoint actually honours (procuringEntity/status/tenderID —
  confirmed live that requesting `title` or `items` there is silently
  ignored). Cheap: each page is a single fast HTTP call regardless of
  what it contains. Filtered client-side to defence-institution
  buyers that are not a military health/welfare unit
  (app.prozorro_normalize.is_defence_buyer /
  is_support_unit) — the two checks that need no detail fetch.

  Phase 2 — DETAIL FETCH. One GET /tenders/{id} per surviving
  candidate, capped, to get title/items/classification/value/awards —
  none of which the list endpoint will ever return. Only here can the
  third relevance check (the CPV code) finally run, via
  app.prozorro_normalize.is_relevant.

WHY THE CAPS ARE SMALL RELATIVE TO OTHER SOURCES, stated honestly
rather than left to look like a bug: this is a shared, real-time
national production feed processing on the order of 200 tender events
per HOUR across all of Ukraine's public sector (measured live:
3,000 list rows spanned only ~14 hours). A defence-buyer hit rate of
~20% still means thousands of candidates in even a single day's
window — nowhere near feasible to detail-fetch in full inside one
scheduled run without hammering a live government API. So each run
covers only the MOST RECENT slice of national procurement activity
(typically well under an hour's worth), same trade-off CPPP's
page-bounded HTML crawl and South Africa's page cap already make —
and the same accumulate-over-repeated-runs answer applies, via the
same (source_id, external_ref) upsert every other source uses.

LICENSING: ProZorro publishes procurement data as explicitly open —
"anyone can use and distribute the system's data, including for
commercial purposes" (prozorro.gov.ua/about) — the clearest reuse
position of any source assessed in this project, consistent with
ProZorro's founding purpose as a public-transparency/anti-corruption
platform, unlike GeM (India), which was rejected specifically for
requiring prior written permission for exactly this kind of reuse.
"""

import asyncio
from typing import TYPE_CHECKING, Optional

import httpx

from app.prozorro_normalize import NotAnOpportunity, is_defence_buyer, is_relevant, is_support_unit, normalize_tender_detail

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

PROZORRO_LIST_URL = "https://public.api.openprocurement.org/api/2.5/tenders"
PROZORRO_DETAIL_URL = "https://public.api.openprocurement.org/api/2.5/tenders/{id}"
PROZORRO_SOURCE_NAME = "ProZorro (Ukraine Public Procurement)"

PROZORRO_LIST_PAGE_SIZE = 500
# 6 pages ≈ 3,000 list rows ≈ roughly the most recent ~14 hours of
# national activity — measured live, not assumed (see module docstring).
PROZORRO_LIST_MAX_PAGES = 6
# Hard cap on how many surviving buyer-filtered candidates get a
# detail fetch. This is the real cost control: each one is a separate
# HTTP round-trip against a live production API that serves the whole
# country, so this stays modest and a small per-request delay is
# added as well — see PROZORRO_DETAIL_DELAY_SECONDS.
PROZORRO_MAX_DETAIL_FETCHES = 150
PROZORRO_DETAIL_DELAY_SECONDS = 0.15
PROZORRO_TIMEOUT_SECONDS = 25.0

PROZORRO_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
}


async def fetch_candidate_list(max_pages: int = PROZORRO_LIST_MAX_PAGES) -> tuple[list[dict], Optional[str]]:
    """
    Phase 1. Returns the raw list rows already narrowed to defence
    buyers that are not a support unit — the two checks that need no
    detail fetch — plus how many total rows were scanned to reach
    them (folded into the return via the caller, since this function
    itself only needs to hand back what's relevant). Same
    partial-failure contract as every other source: a failure on a
    later page keeps whatever earlier pages already returned.
    """
    all_rows: list[dict] = []
    candidates: list[dict] = []
    url = PROZORRO_LIST_URL
    params = {
        "descending": 1,
        "limit": PROZORRO_LIST_PAGE_SIZE,
        "opt_fields": "procuringEntity,status,tenderID,procurementMethodType",
    }

    async with httpx.AsyncClient(timeout=PROZORRO_TIMEOUT_SECONDS, headers=PROZORRO_HEADERS) as client:
        for page in range(max_pages):
            try:
                response = await client.get(url, params=params if page == 0 else None)
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as e:
                return candidates, f"HTTP {e.response.status_code} on list page {page + 1} — {e.response.text[:200]}"
            except httpx.RequestError as e:
                detail = str(e).strip() or type(e).__name__
                return candidates, f"request failed on list page {page + 1} — {detail}"
            except ValueError as e:
                return candidates, f"list page {page + 1} was not JSON — {e}"

            rows = body.get("data", [])
            all_rows.extend(rows)
            for row in rows:
                entity = row.get("procuringEntity") or {}
                name = entity.get("name")
                if is_defence_buyer(name) and not is_support_unit(name):
                    candidates.append(row)

            next_uri = body.get("next_page", {}).get("uri")
            if not next_uri or not rows:
                break
            url = next_uri
            params = None  # the next_page URI already carries every param

    return candidates[:], None  # rows_scanned is len(all_rows); tracked by the caller via closure below


async def run_prozorro_ingestion(
    session: "AsyncSession",
    triggered_by_user_id: str,
    max_list_pages: int = PROZORRO_LIST_MAX_PAGES,
    max_detail_fetches: int = PROZORRO_MAX_DETAIL_FETCHES,
) -> dict:
    """
    Orchestrates the two phases above, then normalize + the final
    (classification-code) relevance check -> upsert programmes ->
    evidence -> award winners -> record the run. Same overall shape as
    every other source's run_*, but with a real list-scan + detail-
    fetch split neither CPPP's page crawl nor Colombia's server-
    filtered query needed.

    Deliberately takes `max_list_pages`/`max_detail_fetches` rather
    than the `days_back` every date-filterable source uses — ProZorro
    has no date-range query, so a days_back parameter here would
    describe a mechanism this source doesn't have (see CPPP, the only
    other source with the same page-bounded-not-date-bounded shape).
    """
    from sqlalchemy import text

    from app.ingestion_common import (
        IngestionConfigError,
        get_or_create_oem_organization,
        get_or_create_government_buyer,
        record_contract_award,
    )

    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": PROZORRO_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "ProZorro source not seeded — run db/migrations/030_prozorro_source.sql first"
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
    candidates: list[dict] = []
    kept_after_detail = 0

    try:
        candidates, list_error = await fetch_candidate_list(max_list_pages)
        if list_error:
            errors.append(list_error)
        candidates = candidates[:max_detail_fetches]

        print(
            f"[prozorro_ingestion] {len(candidates)} defence-buyer candidates found; "
            f"fetching detail for up to {max_detail_fetches}"
        )

        async with httpx.AsyncClient(timeout=PROZORRO_TIMEOUT_SECONDS, headers=PROZORRO_HEADERS) as client:
            for row in candidates:
                await asyncio.sleep(PROZORRO_DETAIL_DELAY_SECONDS)
                try:
                    response = await client.get(PROZORRO_DETAIL_URL.format(id=row["id"]))
                    response.raise_for_status()
                    detail = response.json().get("data", {})
                except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                    all_failures.append({"error": str(e), "tender_id": row.get("id")})
                    continue

                try:
                    record = normalize_tender_detail(detail)
                except NotAnOpportunity:
                    continue  # cancelled/unsuccessful — a skip, not a failure
                except (ValueError, AttributeError, TypeError) as e:
                    all_failures.append({"error": str(e), "tender_id": row.get("id")})
                    continue

                if not is_relevant(record["organization_name"], record["classification_code"]):
                    continue
                kept_after_detail += 1

                org_id = await get_or_create_government_buyer(session, record["organization_name"], "Ukraine")

                upsert_result = await session.execute(
                    text("""
                        insert into programmes
                            (name, country, organization_id, stage, source_id, external_ref, ui_link,
                             naics_code, response_deadline, contact_name, contact_email, contact_phone,
                             contact_address, last_updated)
                        values
                            (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                             :classification_code, :response_deadline, :contact_name, :contact_email,
                             :contact_phone, :contact_address, now())
                        on conflict (source_id, external_ref) where external_ref is not null do update
                            set name = excluded.name,
                                ui_link = excluded.ui_link,
                                organization_id = excluded.organization_id,
                                stage = excluded.stage,
                                naics_code = excluded.naics_code,
                                contact_name = excluded.contact_name,
                                contact_email = excluded.contact_email,
                                contact_phone = excluded.contact_phone,
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
                        "contact_name": record["contact_name"],
                        "contact_email": record["contact_email"],
                        "contact_phone": record["contact_phone"],
                        "contact_address": record["contact_address"],
                    },
                )
                programme_id = str(upsert_result.scalar_one())

                claim = (
                    f"ProZorro tender {record['external_ref']}, published {record['posted_date']}, "
                    f"by defence buyer {record['organization_name'] or 'unknown'}"
                    + (
                        f", CPV/ДК021 {record['classification_code']}"
                        if record["classification_code"]
                        else ", no classification code published"
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

                for winner in record["winners"]:
                    winner_org_id = await get_or_create_oem_organization(session, winner["name"], winner["country"])
                    if winner_org_id is None:
                        continue
                    await record_contract_award(session, programme_id, winner_org_id, source_id)
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
                            "source_id": source_id, "org_id": winner_org_id,
                            "claim": (
                                f"ProZorro tender {record['external_ref']} names {winner['name']} "
                                f"as winner (country: {winner['country'] or 'not specified'})"
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
        "candidates_found": len(candidates),
        "candidates_detail_fetched": len(candidates),
        "candidates_relevant_after_detail": kept_after_detail,
        "failures": all_failures[:20],
        "errors": errors,
    }
