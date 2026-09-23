"""
CPPP (Central Public Procurement Portal, Government of India)
ingestion — network fetch + database orchestration.

Fourth real ingestion source, and the first covering India. Under
GFR 2017 every central government tender above Rs 25 lakh must be
published on CPPP, which makes it the mandatory disclosure point for
a large share of Indian central procurement — including the Military
Engineer Services, BSF, the service branches, DRDO and the defence
PSUs.

WHY THIS SOURCE AND NOT THE OBVIOUS ALTERNATIVES, recorded here so
the choice isn't re-litigated later:
  - GeM (gem.gov.in) publishes no read API for bids, and its
    Website Policies require prior written permission from the GeM
    SPV to reproduce portal content.
  - DEFPROC (defproc.gov.in), the MoD's own portal, serves a captcha
    on its tender listings rather than data — it is closed to
    automated access by deliberate design.
  - data.gov.in hosts only aggregate procurement STATISTICS (budget
    totals, GeM transaction counts). Verified against its catalog
    API: zero results for 'eprocurement' and zero for 'CPPP'. There
    is no live tender feed there.
CPPP's active-tenders listing is the one Indian route that is both
open to automated access and carries live opportunities.

TWO REAL CONSTRAINTS THIS MODULE IS BUILT AROUND:
  1. No JSON API — the listing is HTML, parsed in
     app/cppp_india_normalize.py. There is no documented contract,
     so the parser is written defensively and a layout change will
     show up as zero rows rather than as wrong data.
  2. The portal rate-limits. Light probing already drew a refused
     connection, so pages are fetched sequentially with a delay
     between them and a hard page cap per run. Do not raise these
     limits to make a run faster.
"""

import asyncio
import base64
import urllib.parse
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cppp_india_normalize import normalize_batch, parse_rows
from app.ingestion_common import (
    IngestionConfigError, get_or_create_government_buyer,
    get_rotation_index, advance_rotation_index,
)

CPPP_LISTING_URL = "https://eprocure.gov.in/cppp/latestactivetendersnew"

# Page 1 lives at the plain listing URL, but every subsequent page is
# reached through the portal's own pager form: a /cpppdata view whose
# `url` parameter is the base64 of the very URL being requested.
# Reproduced exactly as the portal's own pager links encode it —
# passing a bare ?page=N to the listing URL instead returns page 1's
# markup or drops the connection.
CPPP_PAGED_URL = "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata"

# CPPP's own "high value tenders" listing, same table structure as the
# latest-active one. Pulled IN ADDITION to the latest listing because
# it is where substantial contracts surface — the latest-active feed
# is dominated by a constant stream of small Military Engineer
# Services estate work. Note the listing exposes no contract-value
# column, so "high value" is CPPP's own selection, not something this
# code can re-derive or threshold itself.
CPPP_HIGH_VALUE_URL = "https://eprocure.gov.in/cppp/highvaluetenders"
CPPP_HIGH_VALUE_PAGED_URL = "https://eprocure.gov.in/cppp/highvaluetenders/cpppdata"
CPPP_SOURCE_NAME = "CPPP (Central Public Procurement Portal, India)"

# The listing renders 10 tenders per page. Kept as a named constant
# because it is the portal's choice, not ours — there is no page-size
# parameter to raise.
CPPP_ROWS_PER_PAGE = 10

# Deliberately conservative defaults — see constraint (2) above.
# Raised from 20 -> 40 (2026-09), then 40 -> 80 (2026-09, after the
# reconnect + rotation fixes below): with disconnects now recoverable
# instead of run-ending, the original 40-page ceiling was no longer
# protecting against a real failure mode, just leaving coverage on
# the table — a full 40-page run only reached page 6+40=46 into the
# ~3,200-page listing per rotation cycle. Verified live at 80 before
# raising: completed cleanly end to end (`errors: []`) with the same
# reconnect logic absorbing the portal's usual mid-run disconnects,
# no new failure mode introduced.
CPPP_DEFAULT_MAX_PAGES = 80
CPPP_PAGE_DELAY_SECONDS = 1.0
# A live 40-page run disconnected 3 times before this fix, always
# recoverable with a fresh connection — capped so a portal that is
# GENUINELY down (not just session-limited) still gives up eventually
# rather than reconnecting forever.
CPPP_MAX_RECONNECTS = 5

# Rotation — the real reason repeated runs barely grew net-new
# coverage even after the reconnect fix: with no date filter on this
# portal (see module docstring), every run always started at page 1,
# so successive runs mostly re-scanned the SAME top-40 pages rather
# than reaching further into the ~32,000-tender listing. Measured
# live: a full 40-page run added only 3 genuinely new programmes on
# top of 61 already stored — confirming most of those 400 rows were
# re-seen, not new. Same rotation mechanism SAM.gov's NAICS-code
# cycling already uses (app.ingestion_common.get_rotation_index/
# advance_rotation_index), applied to a PAGE OFFSET instead of a code
# group: each run starts half_budget pages further into the listing
# than the last (half_budget = pages_capped_at // 2, since each of
# the two listings gets its own half of the page budget per run —
# NOT the full CPPP_DEFAULT_MAX_PAGES, which is easy to misread this
# as), wrapping back to page 1 after CPPP_ROTATION_TOTAL_GROUPS
# windows. At the current default (80 -> half_budget 40), that's
# 100 x 40 = 4,000 pages of margin above the ~3,200-page estimate at
# ~10 rows/page for 32,000 tenders — a full sweep in ~80 daily runs.
CPPP_ROTATION_KEY = "cppp_india_page_offset"
CPPP_ROTATION_TOTAL_GROUPS = 100

# Upper bound on pages a single run may request, however large a
# max_pages the caller passes. Measured live at the new 80-page
# default: ~8.4s/page including delay and occasional reconnects,
# ~11 minutes wall time total — so 120 pages is closer to 17 minutes,
# not the ~8 originally estimated before that default was raised.
# Past this a synchronous request stops being reasonable.
CPPP_MAX_PAGES_CEILING = 120

# Sent because the portal serves different markup to clients it
# doesn't recognise; this is a plain identifying UA, not an attempt
# to look like something it isn't.
CPPP_HEADERS = {
    "User-Agent": "defence-oi-ingestion/1.0 (public procurement opportunity discovery)",
}


def _paged_url(paged_base: str, page: int) -> str:
    """Builds page N's URL in the form the portal's own pager uses."""
    inner = f"{paged_base}?page={page}"
    encoded = urllib.parse.quote(base64.b64encode(inner.encode()).decode(), safe="")
    return f"{paged_base}?url={encoded}"


async def fetch_listing_pages(
    max_pages: int = CPPP_DEFAULT_MAX_PAGES,
    delay_seconds: float = CPPP_PAGE_DELAY_SECONDS,
    first_page_url: str = CPPP_LISTING_URL,
    paged_base: str = CPPP_PAGED_URL,
    start_page: int = 1,
) -> tuple[list[dict], Optional[str]]:
    """
    Walks the active-tenders listing page by page, parsing rows as it
    goes. Stops on the first page that yields no rows (the end of the
    listing, or a layout change), on reaching max_pages, or on a
    network/HTTP failure.

    Same partial-failure contract as the TED and UK sources: a
    failure part-way through returns the rows already collected
    alongside the error, rather than discarding a partly-successful
    run.
    """
    all_rows: list[dict] = []
    error: Optional[str] = None
    # follow_redirects is required, not cosmetic: the listing answers
    # with a 302 to its own /cpppdata view, so without this every
    # page returns a redirect stub and the run silently yields zero
    # rows.
    #
    # RECONNECTS RATHER THAN GIVING UP ON A DISCONNECT — a real,
    # measured pattern, not a guess: live runs consistently failed
    # with "Server disconnected without sending a response" around
    # page 19-22, and each page genuinely takes ~15-16s (the portal
    # itself is slow, same class of problem as South Africa's OCDS
    # API before that fix), putting the failure at roughly 300-350s of
    # SUSTAINED connection time — a session/connection-duration limit,
    # not a per-request timeout (a timeout would raise a different
    # exception) and not this ingestion's own 1s inter-page delay. A
    # brand-new httpx.AsyncClient (a fresh TCP connection) genuinely
    # works again immediately afterward, so a disconnect is treated as
    # "reconnect and carry on from the next page" up to
    # CPPP_MAX_RECONNECTS times, rather than aborting a run that has
    # 30,000+ tenders still ahead of it after page 20.
    client = httpx.AsyncClient(timeout=30.0, headers=CPPP_HEADERS, follow_redirects=True)
    reconnects_used = 0
    last_page = start_page + max_pages - 1
    try:
        page = start_page
        while page <= last_page:
            url = first_page_url if page == 1 else _paged_url(paged_base, page)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                error = f"HTTP {e.response.status_code} on page {page} — {e.response.text[:150]}"
                break
            except httpx.RequestError as e:
                # httpx timeout/connection exceptions can carry an
                # empty str() — the same trap fixed for South Africa's
                # ingestion (see app.south_africa_ingestion._request_error)
                # and re-found here live: a failed run reported
                # "request failed ... —" with nothing after the dash.
                detail = str(e).strip() or type(e).__name__
                if reconnects_used < CPPP_MAX_RECONNECTS:
                    reconnects_used += 1
                    print(
                        f"[cppp_india_ingestion] page {page}: connection dropped ({detail}) — "
                        f"reconnecting (attempt {reconnects_used}/{CPPP_MAX_RECONNECTS})"
                    )
                    await client.aclose()
                    client = httpx.AsyncClient(timeout=30.0, headers=CPPP_HEADERS, follow_redirects=True)
                    await asyncio.sleep(delay_seconds)
                    continue  # retry the SAME page on the new connection
                error = f"request failed on page {page} after {reconnects_used} reconnects — {detail}"
                break

            page_rows = parse_rows(response.text)
            print(f"[cppp_india_ingestion] page {page}: {len(page_rows)} rows")
            if not page_rows:
                break
            all_rows.extend(page_rows)
            page += 1

            if page <= last_page:
                await asyncio.sleep(delay_seconds)
    finally:
        await client.aclose()

    return all_rows, error


async def run_cppp_india_ingestion(
    session: AsyncSession,
    triggered_by_user_id: str,
    max_pages: int = CPPP_DEFAULT_MAX_PAGES,
) -> dict:
    """
    Orchestrates: page through the listing -> normalize and filter to
    defence-publishing organisations -> upsert into programmes ->
    attach evidence -> record the run. Same shape as every other
    source's run_* function, so it drops into INGESTION_SOURCES with
    no scheduler or status-endpoint changes.
    """
    source_result = await session.execute(
        text("select id from sources where name = :name"), {"name": CPPP_SOURCE_NAME}
    )
    source_row = source_result.first()
    if source_row is None:
        raise IngestionConfigError(
            "CPPP source not seeded — run db/migrations/014_cppp_india_source.sql first"
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

    rotation_index = await get_rotation_index(session, CPPP_ROTATION_KEY)

    total_ingested = 0
    all_failures = []
    errors = []
    # Hard ceiling. CPPP holds roughly 3,200 pages of active tenders,
    # but a single synchronous run cannot sweep them: each page costs
    # a fetch plus the deliberate delay, so even this ceiling is
    # several minutes of wall time. Full coverage is meant to
    # accumulate across scheduled daily runs (the upsert is
    # idempotent on external_ref), not to be forced into one request.
    pages_capped_at = min(max_pages, CPPP_MAX_PAGES_CEILING)
    # Both listings get roughly this many pages each (split below) —
    # the rotation offset is computed against THAT half-budget, since
    # that is genuinely how many pages each call advances per run.
    half_budget = max(1, pages_capped_at // 2)
    start_page = rotation_index * half_budget + 1
    print(f"[cppp_india_ingestion] rotation index {rotation_index} -> starting at page {start_page}")

    try:
        # High-value first: it is the listing most likely to carry
        # substantial contracts, so it gets its share of the page
        # budget even if the latest-active pass later hits an error.
        high_value_rows, hv_error = await fetch_listing_pages(
            max_pages=half_budget,
            first_page_url=CPPP_HIGH_VALUE_URL,
            paged_base=CPPP_HIGH_VALUE_PAGED_URL,
            start_page=start_page,
        )
        if hv_error:
            errors.append(f"high-value listing: {hv_error}")

        latest_rows, latest_error = await fetch_listing_pages(
            max_pages=pages_capped_at - half_budget if pages_capped_at - half_budget > 0 else half_budget,
            start_page=start_page,
        )
        if latest_error:
            errors.append(f"latest-active listing: {latest_error}")

        # Deduped here as well as by the DB upsert, so rows appearing
        # in both listings aren't normalized and written twice.
        raw_rows = high_value_rows + latest_rows
        seen_refs = set()
        deduped_rows = []
        for row in raw_rows:
            ref = (row.get("title_cell") or "").rsplit("/", 1)[-1].strip()
            if ref and ref in seen_refs:
                continue
            if ref:
                seen_refs.add(ref)
            deduped_rows.append(row)

        normalized, failures = normalize_batch(deduped_rows)
        all_failures.extend(failures)
        print(
            f"[cppp_india_ingestion] rows fetched: {len(raw_rows)} "
            f"(high-value {len(high_value_rows)}, latest {len(latest_rows)}), "
            f"unique: {len(deduped_rows)}, kept after defence-org + "
            f"facilities-works filters: {len(normalized)}"
        )

        for record in normalized:
            org_id = await get_or_create_government_buyer(session, record["organization_name"], "India")

            upsert_result = await session.execute(
                text("""
                    insert into programmes
                        (name, country, organization_id, stage, source_id, external_ref, ui_link,
                         naics_code, response_deadline, last_updated)
                    values
                        (:name, :country, :organization_id, :stage, :source_id, :external_ref, :ui_link,
                         :classification_code, :response_deadline, now())
                    on conflict (source_id, external_ref) where external_ref is not null do update
                        set name = excluded.name,
                            ui_link = excluded.ui_link,
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
                    "ui_link": record.get("ui_link"),
                    "classification_code": record["classification_code"],
                    "response_deadline": record["response_deadline"],
                },
            )
            programme_id = str(upsert_result.scalar_one())

            # The claim records WHY this counts as defence procurement
            # — the publishing organisation — because that, not a
            # category code, is the actual evidence for this source.
            claim = (
                f"CPPP tender {record['external_ref']}, published {record['posted_date']}, "
                f"bid closing {record['response_deadline']}, "
                f"published by Indian defence organisation: {record['organization_name'] or 'unknown'}"
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

        # Advanced on a genuine attempt regardless of a partial page
        # error (same philosophy as every other rotating source's
        # advance call: a config error never advances, but the portal
        # being slow/flaky on one page must not stall the sweep at the
        # same offset forever).
        await advance_rotation_index(session, CPPP_ROTATION_KEY, CPPP_ROTATION_TOTAL_GROUPS)

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
        "rows_examined": len(raw_rows),
        "failures": all_failures,
        "errors": errors,
        "pages_requested": pages_capped_at,
        "start_page": start_page,
        "rotation_index": rotation_index,
    }
