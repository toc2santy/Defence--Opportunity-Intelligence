"""
UK Find a Tender ingestion — network fetch + database orchestration.

REAL DIFFERENCE FROM SAM.GOV: no API key needed at all (confirmed —
the official docs show no auth header anywhere for this endpoint),
and no NAICS-style rotation, because this API has no server-side
category filter to rotate through — one date-range call naturally
covers every category, filtered for defense-relevance client-side
in app/uk_ft_normalize.py.

PAGINATION: this OCDS API returns a `links.next` cursor URL (already
absolute and ready to fetch, not something to rebuild query params
for) rather than a page number or token field — confirmed live by
walking several pages and checking for duplicate release ids (none
found). `stages` also needs to be sent as a repeated query parameter
(?stages=planning&stages=tender&stages=award), not one comma-joined
value — the API silently returns zero results for the comma-joined
form instead of erroring, which is what made this source look broken
for a while.
"""

from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.uk_ft_normalize import normalize_batch
from app.ingestion_common import (
    IngestionConfigError, get_or_create_oem_organization, get_or_create_government_buyer, record_contract_award,
)

UK_FT_BASE_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
UK_FT_SOURCE_NAME = "UK Find a Tender Service"


# Safety cap on total releases pulled per ingestion run, across all
# pages — mirrors the same guard added to the TED source. Keeps one
# run bounded even on a date range wide enough to have several
# hundred updated releases before client-side defense filtering.
UK_FT_MAX_RELEASES_PER_RUN = 1000


async def fetch_all_releases(
    updated_from: str,
    updated_to: str,
    stages: str = "planning,tender,award",
    max_releases: int = UK_FT_MAX_RELEASES_PER_RUN,
) -> tuple[list[dict], Optional[str]]:
    """
    Follows the API's own OCDS `links.next` cursor URL (an absolute,
    ready-to-fetch URL — confirmed live, not something to rebuild
    params for) until it's absent, a page comes back with no
    releases, or max_releases is reached.

    Same partial-failure handling as TED's fetch_all_notices: a
    failure on any page after the first stops pagination but keeps
    whatever earlier pages already returned, rather than discarding
    a partially-successful run.
    """
    all_releases: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                UK_FT_BASE_URL,
                params={
                    "updatedFrom": updated_from,
                    "updatedTo": updated_to,
                    "stages": stages.split(","),
                    "limit": 100,
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as e:
            retry_after = e.response.headers.get("Retry-After")
            return [], (
                f"HTTP {e.response.status_code} — {e.response.text[:200]}"
                + (f" (Retry-After: {retry_after}s)" if retry_after else "")
            )
        except httpx.RequestError as e:
            return [], f"request failed — {e}"

        while True:
            page_releases = body.get("releases", [])
            all_releases.extend(page_releases)
            next_url = body.get("links", {}).get("next")
            if not next_url or not page_releases or len(all_releases) >= max_releases:
                break
            try:
                response = await client.get(next_url)
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as e:
                return all_releases[:max_releases], f"HTTP {e.response.status_code} — {e.response.text[:200]}"
            except httpx.RequestError as e:
                return all_releases[:max_releases], f"request failed — {e}"

    return all_releases[:max_releases], None


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
        raw_releases, fetch_error = await fetch_all_releases(updated_from_str, updated_to_str, stages)
        if fetch_error:
            errors.append(fetch_error)

        normalized, failures = normalize_batch(raw_releases)
        all_failures.extend(failures)

        for record in normalized:
            org_id = await get_or_create_government_buyer(session, record["organization_name"], "United Kingdom")

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, naics_code, ui_link,
                         contact_name, contact_email, contact_phone, contact_address,
                         set_aside_code, set_aside_description, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :classification_code, :ui_link,
                         :contact_name, :contact_email, :contact_phone, :contact_address,
                         :set_aside_code, :set_aside_description, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            ui_link = excluded.ui_link,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            contact_name = excluded.contact_name,
                            contact_email = excluded.contact_email,
                            contact_phone = excluded.contact_phone,
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
                    "classification_code": record["classification_code"],
                    "ui_link": record.get("ui_link"),
                    "contact_name": record.get("contact_name"),
                    "contact_email": record.get("contact_email"),
                    "contact_phone": record.get("contact_phone"),
                    "contact_address": record.get("contact_address"),
                    "set_aside_code": record.get("set_aside_code"),
                    "set_aside_description": record.get("set_aside_description"),
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

            # OEM Intelligence — only populated when the release names
            # 'supplier'-role parties, which in practice means only
            # award-stage releases (extract_winners returns [] for a
            # release with no such parties).
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
                            f"UK Find a Tender notice {record['external_ref']} names "
                            f"{winner['name']} as winner (country: {winner['country'] or 'not specified'})"
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
        "date_range": {"from": updated_from_str, "to": updated_to_str},
    }
