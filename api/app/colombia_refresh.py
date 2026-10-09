"""
Status refresh for already-stored SECOP II (Colombia) tenders (2026-10).

Why it exists: app/colombia_ingestion.py only fetches a window of recently
PUBLISHED procedures, so a stored procedure that is later awarded is never
re-read. Found live on a user-reported tender: it was stored as an open request
for proposals (stage rfp_issued, no deadline) although SECOP II already showed it
awarded (adjudicado = Si, winner and award date published), and the portal page
therefore did not show it as an open tender.

This re-reads stored procedures by id from the same open dataset and corrects
only what the first ingestion got wrong or never set:
  - stage          -> contract_awarded when the procedure is awarded
  - response_deadline -> the published closing date

It never deletes, and never touches name, buyer, code, link or contacts. A
procedure the dataset now lists as cancelled/void (stage None) is only COUNTED:
programmes.stage has no "called off" value (same reasoning as
NON_INGESTABLE_STATUSES) and removing rows is not this job's call.

`dry_run=True` reports exactly what would change and writes nothing.

The compare-and-correct loop itself now lives in app/source_reconcile.py (shared with
Paraguay); this module keeps the SECOP II fetch-by-id and the original entry point.
"""

import asyncio
import os

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.colombia_ingestion import (
    COLOMBIA_BASE_URL,
    COLOMBIA_HEADERS,
    COLOMBIA_MAX_RETRIES,
    COLOMBIA_RETRY_BACKOFF_SECONDS,
    COLOMBIA_SOURCE_NAME,
    COLOMBIA_TIMEOUT_SECONDS,
)
from app.colombia_normalize import COL_PROCESS_ID

IDS_PER_REQUEST = 40          # keeps the $where URL comfortably short
REQUEST_DELAY_SECONDS = 0.5

# Re-exported: the pure decision used to live here.
from app.source_reconcile import plan_changes  # noqa: E402,F401


async def _fetch_by_ids(client: httpx.AsyncClient, ids: list[str]) -> list[dict]:
    headers = dict(COLOMBIA_HEADERS)
    token = os.getenv("SOCRATA_APP_TOKEN", "").strip()
    if token:
        headers["X-App-Token"] = token
    quoted = ",".join("'" + i.replace("'", "") + "'" for i in ids)
    params = {"$where": f"{COL_PROCESS_ID} in({quoted})", "$limit": str(len(ids) * 2)}
    for attempt in range(1, COLOMBIA_MAX_RETRIES + 1):
        resp = await client.get(COLOMBIA_BASE_URL, params=params, headers=headers, timeout=COLOMBIA_TIMEOUT_SECONDS)
        if resp.status_code == 429 and attempt < COLOMBIA_MAX_RETRIES:
            await asyncio.sleep(COLOMBIA_RETRY_BACKOFF_SECONDS * attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    return []


async def refresh_colombia_status(session: AsyncSession, dry_run: bool = False) -> dict:
    from app.source_reconcile import reconcile_source
    return await reconcile_source(session, COLOMBIA_SOURCE_NAME, dry_run=dry_run)
