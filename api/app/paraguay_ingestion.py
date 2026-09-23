"""
DNCP Paraguay ingestion — network fetch + database orchestration.

Tenth real source. See db/migrations/039 for what was verified live
before building this, including the one genuinely new engineering
problem this source has that no other source here does: its own API
returns an HTTP 200 whose JSON body is INTERMITTENTLY TRUNCATED
mid-object. That is not a timeout and not a dropped connection (both
already-handled failure shapes, see cppp_india_ingestion.py and
south_africa_ingestion.py) — the request completes normally and the
body itself is corrupt, so `_fetch_page` treats a JSON parse failure
as a retryable condition and re-requests the SAME page, the same
resume-don't-abandon spirit as CPPP's reconnect logic but for a
different underlying failure.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import httpx

from app.paraguay_normalize import extract_classification_from_detail, extract_eligibility_from_detail, normalize_batch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

PARAGUAY_SOURCE_NAME = "DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)"
PARAGUAY_SEARCH_URL = "https://www.contrataciones.gov.py/datos/api/v3/doc/search/processes"
PARAGUAY_RECORD_URL = "https://www.contrataciones.gov.py/datos/api/v3/doc/ocds/record"

# /search/processes's own compiledRelease carries no items at all —
# confirmed live (db/migrations/039) — so a classification code can
# only be read via a second, per-candidate detail fetch, the same
# two-phase shape ProZorro already uses (its list endpoint is equally
# thin). Capped independently of PARAGUAY_MAX_PAGES because each
# candidate costs its own HTTP round trip.
# Lowered from 60 after a live run: at 60 candidates, the combined
# search + per-candidate detail phase exceeded 400s and the run had
# to be abandoned mid-flight (caught by _reap_orphaned_ingestion_jobs
# at the next startup, not a data-correctness problem, but a poor
# single-request duration). 20 keeps a run comfortably under two
# minutes even with retries; coverage still accumulates over
# scheduled re-runs the same way every other source's page/detail cap
# does.
PARAGUAY_MAX_DETAIL_FETCHES = 20
PARAGUAY_DETAIL_DELAY_SECONDS = 0.2

# The single search term that works: DNCP prints every command's name
# compounded with its parent, e.g. "Comando de la Fuerza Aerea Uoc 4 /
# Ministerio de Defensa Nacional" — confirmed live that the API's own
# name filter matches as a substring, so this one query reaches every
# named command under the Ministry without a separate call per unit.
PARAGUAY_BUYER_QUERY = "Ministerio de Defensa Nacional"

# Kept modest — confirmed live that larger pages truncate more often,
# though even 10 truncates occasionally (handled by the retry below,
# not by shrinking further).
PARAGUAY_ITEMS_PER_PAGE = 10
# Lowered from 15 alongside PARAGUAY_MAX_DETAIL_FETCHES — the search
# phase alone already took ~2 minutes at 15 pages; 8 keeps the whole
# run (search + detail) comfortably bounded in one request.
PARAGUAY_MAX_PAGES = 8
PARAGUAY_TIMEOUT_SECONDS = 25.0
PARAGUAY_MAX_PARSE_RETRIES = 3
PARAGUAY_RETRY_BACKOFF_SECONDS = 2.0
# A live 90-day run disconnected outright after the first page
# ("Server disconnected without sending a response") — a different
# failure shape from the mid-JSON truncation above, closer to CPPP's
# connection-duration limit. Reconnects with a fresh client and
# resumes the SAME page, same recovery shape as CPPP's
# CPPP_MAX_RECONNECTS, capped so a genuinely down portal still gives
# up eventually.
PARAGUAY_MAX_RECONNECTS = 5

PARAGUAY_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
}


class _Disconnected(Exception):
    """Raised to signal the caller should reconnect and retry the same page."""


async def _fetch_page(client: httpx.AsyncClient, params: dict) -> tuple[list[dict], Optional[str]]:
    for attempt in range(PARAGUAY_MAX_PARSE_RETRIES):
        try:
            response = await client.get(PARAGUAY_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as e:
            return [], f"HTTP {e.response.status_code} — {e.response.text[:200]}"
        except httpx.RequestError as e:
            # A dropped connection, not a corrupt body — the caller
            # reconnects with a fresh client and retries this same
            # page, the same recovery shape as CPPP's reconnect logic.
            detail = str(e).strip() or type(e).__name__
            raise _Disconnected(detail) from e
        except ValueError:
            # A genuinely corrupt/truncated body on an otherwise
            # normal 200 — see this module's own docstring. Retryable,
            # not a hard failure, since re-requesting the same page
            # very often comes back clean.
            if attempt < PARAGUAY_MAX_PARSE_RETRIES - 1:
                await asyncio.sleep(PARAGUAY_RETRY_BACKOFF_SECONDS)
                continue
            return [], "response body truncated after all retries"
        else:
            records = payload.get("records") or []
            return [r.get("compiledRelease") for r in records if r.get("compiledRelease")], None
    return [], "exhausted retries"


async def fetch_processes(
    days_back: int = 90,
    max_pages: int = PARAGUAY_MAX_PAGES,
) -> tuple[list[dict], Optional[str]]:
    """
    Pages through /search/processes with $page. Same partial-failure
    contract as every other source: a failure on a later page stops
    paging but keeps whatever earlier pages already returned.
    """
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    compiled_releases: list[dict] = []
    error: Optional[str] = None

    client = httpx.AsyncClient(timeout=PARAGUAY_TIMEOUT_SECONDS, headers=PARAGUAY_HEADERS)
    reconnects = 0
    try:
        page = 1
        while page <= max_pages:
            params = {
                "tender.procuringEntity.name": PARAGUAY_BUYER_QUERY,
                "fecha_desde": date_from,
                "fecha_hasta": date_to,
                "tipo_fecha": "publicacion_llamado",
                "items_per_page": PARAGUAY_ITEMS_PER_PAGE,
                "page": page,
            }
            try:
                page_records, page_error = await _fetch_page(client, params)
            except _Disconnected as e:
                reconnects += 1
                if reconnects > PARAGUAY_MAX_RECONNECTS:
                    error = f"request failed — {e} (gave up after {PARAGUAY_MAX_RECONNECTS} reconnects)"
                    break
                await client.aclose()
                client = httpx.AsyncClient(timeout=PARAGUAY_TIMEOUT_SECONDS, headers=PARAGUAY_HEADERS)
                continue  # retry the SAME page, not advanced

            if page_error:
                error = page_error
                break
            if not page_records:
                break
            compiled_releases.extend(page_records)
            if len(page_records) < PARAGUAY_ITEMS_PER_PAGE:
                break  # last page
            page += 1
    finally:
        await client.aclose()

    return compiled_releases, error


async def fetch_classification_codes(ocids: list[str]) -> dict[str, dict]:
    """
    One GET /ocds/record/{ocid} per candidate, AFTER the buyer/
    category filter has already narrowed the field — capped at
    PARAGUAY_MAX_DETAIL_FETCHES, same cost-control reasoning as
    ProZorro's PROZORRO_MAX_DETAIL_FETCHES. A candidate whose detail
    fetch fails (truncated body even after retries, a dropped
    connection, a real HTTP error) simply gets no code — this is a
    best-effort enrichment, not something that should fail the whole
    run over one bad record.

    Also extracts eligibility (Eligibility/Backup Phase 1, 2026-09)
    from the SAME payload — `eligibilityCriteria` lives at the exact
    same `records[0].compiledRelease.tender` path this function's own
    docstring already reads for classification, so no second detail
    fetch is needed. Kept as one dict-of-dicts return (still keyed by
    ocid) rather than two separate dicts, so a caller can never end up
    with a classification result and an eligibility result computed
    from two DIFFERENT fetch attempts of the same ocid.
    """
    details: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=PARAGUAY_TIMEOUT_SECONDS, headers=PARAGUAY_HEADERS) as client:
        for ocid in ocids[:PARAGUAY_MAX_DETAIL_FETCHES]:
            for attempt in range(PARAGUAY_MAX_PARSE_RETRIES):
                try:
                    response = await client.get(f"{PARAGUAY_RECORD_URL}/{ocid}")
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPStatusError, httpx.RequestError):
                    break
                except ValueError:
                    if attempt < PARAGUAY_MAX_PARSE_RETRIES - 1:
                        await asyncio.sleep(PARAGUAY_RETRY_BACKOFF_SECONDS)
                        continue
                    break
                else:
                    set_aside_code, set_aside_description = extract_eligibility_from_detail(payload)
                    details[ocid] = {
                        "code": extract_classification_from_detail(payload),
                        "set_aside_code": set_aside_code,
                        "set_aside_description": set_aside_description,
                    }
                    break
            await asyncio.sleep(PARAGUAY_DETAIL_DELAY_SECONDS)
    return details


async def run_paraguay_ingestion(
    session: "AsyncSession",
    triggered_by_user_id: str,
    days_back: int = 90,
) -> dict:
    """
    Orchestrates: search fetch -> normalize + buyer/category filter ->
    per-candidate classification detail fetch -> upsert programmes ->
    evidence -> award winner -> record the run. Same shape as every
    other source's run_*.
    """
    from sqlalchemy import text

    from app.ingestion_common import (
        IngestionConfigError,
        get_or_create_oem_organization,
        get_or_create_government_buyer,
        record_contract_award,
    )

    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": PARAGUAY_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "DNCP Paraguay source not seeded — run db/migrations/039_paraguay_source.sql first"
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
        raw_releases, fetch_error = await fetch_processes(days_back=days_back)
        if fetch_error:
            errors.append(fetch_error)

        normalized, failures = normalize_batch(raw_releases)
        all_failures.extend(failures)
        print(
            f"[paraguay_ingestion] {len(raw_releases)} releases -> {len(normalized)} kept "
            "after the defence-buyer + non-materiel-category filters"
        )

        details = await fetch_classification_codes([r["external_ref"] for r in normalized])
        codes_found = sum(1 for d in details.values() if d.get("code"))
        restrictions_found = sum(1 for d in details.values() if d.get("set_aside_code"))
        print(
            f"[paraguay_ingestion] classification detail fetch: {codes_found}/{len(normalized)} got a real UNSPSC code, "
            f"{restrictions_found} carried a stated eligibility restriction"
        )
        for record in normalized:
            detail = details.get(record["external_ref"], {})
            code = detail.get("code")
            record["classification_code"] = code
            record["classification_scheme"] = "UNSPSC" if code else None
            record["set_aside_code"] = detail.get("set_aside_code")
            record["set_aside_description"] = detail.get("set_aside_description")

        for record in normalized:
            org_id = await get_or_create_government_buyer(
                session, record["organization_name"], "Paraguay"
            )

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, naics_code,
                         set_aside_code, set_aside_description, ui_link, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :classification_code,
                         :set_aside_code, :set_aside_description, :ui_link, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            organization_id = excluded.organization_id,
                            stage = excluded.stage,
                            naics_code = excluded.naics_code,
                            set_aside_code = excluded.set_aside_code,
                            set_aside_description = excluded.set_aside_description,
                            ui_link = excluded.ui_link,
                            last_updated = now()
                    returning id
                """),
                {
                    "name": record["name"], "country": record["country"],
                    "organization_id": org_id, "stage": record["stage"],
                    "source_id": source_id, "external_ref": record["external_ref"],
                    "classification_code": record["classification_code"],
                    "set_aside_code": record.get("set_aside_code"),
                    "set_aside_description": record.get("set_aside_description"),
                    "ui_link": record.get("ui_link"),
                },
            )
            programme_id = str(upsert_result.scalar_one())

            claim = (
                f"DNCP Paraguay process {record['external_ref']}, "
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
                    session, record["winner_name"], "Paraguay"
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
                            "source_id": source_id,
                            "org_id": winner_org_id,
                            "claim": (
                                f"DNCP Paraguay process {record['external_ref']} names "
                                f"{record['winner_name']} as the awarded supplier"
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
