"""
CanadaBuys ingestion — network fetch + database orchestration.

Fifth real ingestion source, and the strongest addition since the
original three. Unlike every scraped source, this is a published open
dataset under the Open Government Licence — Canada explicitly permits
reuse, so there is no outstanding permission question of the kind
recorded against CPPP and GeM.

WHY THE 'open' FEED AND NOT THE 'new' ONE — worth stating because the
filenames invite the wrong choice:
  newTenderNotice-nouvelAvisAppelOffres.csv  is a rolling delta of
      notices published since the last 2-hourly refresh. A live pull
      returned 32 rows one minute and 5 the next. Polling it is how
      you'd track changes; it is NOT the current opportunity set.
  openTenderNotice-ouvertAvisAppelOffres.csv is every tender
      currently open for bidding — 976 rows in a live pull, 824 of
      them carrying UNSPSC codes. That is the set this platform wants.

One HTTP request fetches the whole feed, so there is no pagination,
no rotation and no rate-limit pacing to worry about — the opposite of
the CPPP source in every respect.
"""

import csv
import io
from datetime import date
from typing import TYPE_CHECKING, Optional

import httpx

from app.canada_buys_normalize import normalize_award_batch, normalize_batch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

CANADA_BUYS_OPEN_TENDERS_URL = (
    "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"
)
CANADA_BUYS_SOURCE_NAME = "CanadaBuys (Government of Canada)"

# Award notices are published as a SEPARATE file from the open-tenders
# one above, per Government of Canada fiscal year (April 1 - March
# 31) — confirmed live via open.canada.ca's own dataset page, not
# guessed at. Deliberately fetches the CURRENT fiscal year's file
# only, not "...Complete...csv" (every award since 2022-08, which
# timed out at 40s in testing — clearly a much larger file) — this
# platform wants recent award evidence for Competitor/OEM
# Intelligence, not the full historical archive.
CANADA_BUYS_AWARD_URL_TEMPLATE = (
    "https://canadabuys.canada.ca/opendata/pub/{fy}-awardNotice-avisAttribution.csv"
)


def current_fiscal_year_label(today: Optional[date] = None) -> str:
    """
    Government of Canada fiscal year runs April 1 - March 31, so
    "2026-2027" starts in April 2026, not January. `today` is a
    parameter purely for testability, same pattern as
    app/next_best_action.py's suggest_next_action.
    """
    today = today or date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{start_year + 1}"

# The full open-tenders feed is a few MB and the server can be slow to
# start streaming it; this is deliberately more generous than the
# 30s used elsewhere.
CANADA_BUYS_TIMEOUT_SECONDS = 120.0

# Required, not cosmetic: the CDN in front of the open-data files
# answers httpx's default User-Agent with a 403. A plain identifying
# UA is served normally. Same class of trap as the redirect handling
# in the CPPP source — the request "works in curl" and fails here for
# a reason that has nothing to do with the data.
CANADA_BUYS_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
    "Accept": "text/csv,*/*",
}


async def fetch_open_tenders() -> tuple[list[dict], Optional[str]]:
    """
    Downloads and parses the open-tenders CSV. Returns parsed rows
    plus an optional error string, matching the partial-failure
    contract the other sources use — though with a single request
    there is no partial success to preserve, so a failure yields no
    rows.

    utf-8-sig handles the byte-order mark the feed ships with; without
    it the very first column name arrives as '\\ufefftitle-titre-eng'
    and every lookup against it silently misses.
    """
    async with httpx.AsyncClient(
        timeout=CANADA_BUYS_TIMEOUT_SECONDS, follow_redirects=True, headers=CANADA_BUYS_HEADERS
    ) as client:
        try:
            response = await client.get(CANADA_BUYS_OPEN_TENDERS_URL)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return [], f"HTTP {e.response.status_code} — {e.response.text[:150]}"
        except httpx.RequestError as e:
            return [], f"request failed — {e}"

    body = response.content.decode("utf-8-sig", errors="ignore")
    rows = list(csv.DictReader(io.StringIO(body)))
    print(f"[canada_buys_ingestion] parsed {len(rows)} open tenders from feed")
    return rows, None


async def fetch_award_notices(fy_label: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    """
    Same fetch shape as fetch_open_tenders — one request, no
    pagination — against the current fiscal year's award file.
    """
    fy_label = fy_label or current_fiscal_year_label()
    url = CANADA_BUYS_AWARD_URL_TEMPLATE.format(fy=fy_label)
    async with httpx.AsyncClient(
        timeout=CANADA_BUYS_TIMEOUT_SECONDS, follow_redirects=True, headers=CANADA_BUYS_HEADERS
    ) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return [], f"HTTP {e.response.status_code} on award file {fy_label} — {e.response.text[:150]}"
        except httpx.RequestError as e:
            return [], f"request failed on award file {fy_label} — {e}"

    body = response.content.decode("utf-8-sig", errors="ignore")
    rows = list(csv.DictReader(io.StringIO(body)))
    print(f"[canada_buys_ingestion] parsed {len(rows)} award notices from {fy_label}")
    return rows, None


async def run_canada_buys_ingestion(
    session: "AsyncSession",
    triggered_by_user_id: str,
) -> dict:
    """
    Orchestrates: fetch the open-tenders feed -> normalize and filter
    to defence buyers -> upsert into programmes -> attach evidence ->
    record the run. Same shape as every other source's run_* function.

    Takes no date/page parameters: the feed is by definition the
    current open set, so there is no window to choose.

    A SECOND phase runs after the open-tenders one: the current fiscal
    year's award notices, which is a genuinely different file (see
    fetch_award_notices) publishing real winner data — CanadaBuys
    previously populated `programmes` and `contact_address` but never
    `contract_awards`, the same real gap Report Intel review found for
    SAM.gov's `pointOfContact`/`officeAddress`. A failure in this
    phase is recorded but does not fail the whole run — the
    open-tenders phase already succeeded and its result must not be
    thrown away because the second, independent file had a problem.
    """
    from sqlalchemy import text

    from app.ingestion_common import (
        IngestionConfigError, get_or_create_oem_organization, get_or_create_government_buyer, record_contract_award,
    )

    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": CANADA_BUYS_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "CanadaBuys source not seeded — run db/migrations/015_canada_buys_source.sql first"
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
    all_failures = []
    errors = []
    raw_rows = []
    award_rows = []

    try:
        raw_rows, fetch_error = await fetch_open_tenders()
        if fetch_error:
            errors.append(fetch_error)

        normalized, failures = normalize_batch(raw_rows)
        all_failures.extend(failures)
        print(
            f"[canada_buys_ingestion] {len(raw_rows)} open tenders -> "
            f"{len(normalized)} defence-relevant after buyer + works filters"
        )

        for record in normalized:
            org_id = await get_or_create_government_buyer(session, record["organization_name"], "Canada")

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, ui_link,
                         naics_code, response_deadline,
                         contact_name, contact_email, contact_phone,
                         set_aside_code, set_aside_description, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                         :classification_code, :response_deadline,
                         :contact_name, :contact_email, :contact_phone,
                         :set_aside_code, :set_aside_description, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            ui_link = excluded.ui_link,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            naics_code = excluded.naics_code,
                            response_deadline = excluded.response_deadline,
                            contact_name = excluded.contact_name,
                            contact_email = excluded.contact_email,
                            contact_phone = excluded.contact_phone,
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
                    "contact_name": record.get("contact_name"),
                    "contact_email": record.get("contact_email"),
                    "contact_phone": record.get("contact_phone"),
                    "set_aside_code": record.get("set_aside_code"),
                    "set_aside_description": record.get("set_aside_description"),
                },
            )
            programme_id = str(upsert_result.scalar_one())

            # Every UNSPSC code goes in the claim, not just the one
            # stored in naics_code — otherwise a multi-coded tender
            # loses most of its classification the moment it's written.
            all_codes = record["all_classification_codes"]
            claim = (
                f"CanadaBuys notice {record['external_ref']}, published {record['posted_date']}, "
                f"closing {record['response_deadline']}, "
                f"UNSPSC {', '.join(all_codes) if all_codes else 'none published'}, "
                f"buyer: {record['organization_name'] or 'unknown'}"
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

        # --- Phase 2: award notices, a separate file (see module
        # docstring). Its own errors are appended to `errors` and
        # reported honestly, but never raised — a problem here must
        # not undo the open-tenders phase that already committed.
        try:
            award_rows, award_fetch_error = await fetch_award_notices()
            if award_fetch_error:
                errors.append(award_fetch_error)

            normalized_awards, award_failures = normalize_award_batch(award_rows)
            all_failures.extend(award_failures)
            print(
                f"[canada_buys_ingestion] {len(award_rows)} award rows -> "
                f"{len(normalized_awards)} defence-relevant after buyer + works + cancelled filters"
            )

            for record in normalized_awards:
                org_id = await get_or_create_government_buyer(session, record["organization_name"], "Canada")

                upsert_result = await session.execute(
                    text("""
                        insert into programmes
                            (name, country, organization_id, stage, source_id, external_ref, ui_link,
                             naics_code, contact_name, contact_email, contact_phone, contact_address,
                             last_updated)
                        values
                            (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                             :classification_code, :contact_name, :contact_email, :contact_phone,
                             :contact_address, now())
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
                        "ui_link": record.get("ui_link"),
                        "classification_code": record["classification_code"],
                        "contact_name": record.get("contact_name"),
                        "contact_email": record.get("contact_email"),
                        "contact_phone": record.get("contact_phone"),
                        "contact_address": record.get("contact_address"),
                    },
                )
                programme_id = str(upsert_result.scalar_one())

                claim = (
                    f"CanadaBuys award notice {record['external_ref']}, awarded {record['posted_date']}, "
                    f"buyer: {record['organization_name'] or 'unknown'}"
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
                        session, record["winner_name"], record["winner_country"]
                    )
                    if winner_org_id is not None:
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
                                    f"CanadaBuys award {record['external_ref']} names "
                                    f"{record['winner_name']} as the winning supplier"
                                    + (f" ({record['winner_country']})" if record["winner_country"] else "")
                                ),
                            },
                        )
        except Exception as e:
            # The open-tenders phase above already committed — this
            # phase's own exception is recorded as an error, not
            # re-raised, so it can't roll back real, already-ingested
            # opportunities.
            errors.append(f"award phase failed — {e}")

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
        "awards_recorded": awards_recorded,
        "rows_examined": len(raw_rows) + len(award_rows),
        "failures": all_failures,
        "errors": errors,
    }
