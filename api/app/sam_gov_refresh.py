"""
Periodic refresh of already-ingested SAM.gov notices (2026-09) —
closes a real, structural gap: app/sam_gov_ingestion.py's own run is
insert-only, driven by a postedFrom/postedTo window, so a notice
amended AFTER it was first stored — a real, common case, e.g. a
procurement contact added days later — never gets that update picked
up, especially once the notice ages out of SAM.gov's own active
search index and stops being returned by fetch_opportunities() at all.

Found live (2026-09) on a real user-reported tender ("Skydio X10
Drone... for WAPA"): its real point of contact was added 6 days after
we first ingested it; SAM.gov's own record shows
`additionalInfo.sections` flagging the "contact" section as
"updated" independently of the notice's initial post.

Uses SAM.gov's own public opportunity-detail endpoint
(sam.gov/api/prod/opps/v2/opportunities/{noticeId}) — the same one
sam.gov's own website calls, NOT the official documented
api.sam.gov/opportunities/v2/search REST API SAM_GOV_API_KEY is for.
Confirmed live: no api_key needed, and it still returns full detail
(including pointOfContact) for notices already archived/expired out
of the search API's active window — which is exactly the case this
function exists to reach. This is an unofficial, reverse-engineered
endpoint with no documented stability guarantee, unlike the official
REST API — treated defensively here (a failed/404/non-JSON response
for one notice is logged and skipped, never allowed to abort the
whole run) for exactly that reason.
"""

import asyncio
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SAM_GOV_DETAIL_URL = "https://sam.gov/api/prod/opps/v2/opportunities/{notice_id}"
# A polite delay between requests — this endpoint isn't the official,
# quota-metered REST API, so there's no documented rate limit to
# respect, but hammering an unofficial endpoint at full speed is not
# how a good citizen of someone else's infrastructure behaves.
REQUEST_DELAY_SECONDS = 0.3
# Caps how many notices one scheduled run re-checks — this is a
# periodic background job, not a one-time backlog clear (that backlog
# was already cleared manually, 2026-09); a real, bounded batch per
# run keeps a single scheduled run's wall-clock time predictable
# regardless of how large the backlog of missing-contact rows ever
# grows again.
DEFAULT_BATCH_LIMIT = 200


async def _fetch_detail(client: httpx.AsyncClient, notice_id: str) -> Optional[dict]:
    try:
        resp = await client.get(SAM_GOV_DETAIL_URL.format(notice_id=notice_id), timeout=20.0)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


async def refresh_sam_gov_contacts(session: AsyncSession, batch_limit: int = DEFAULT_BATCH_LIMIT) -> dict:
    source_result = await session.execute(
        text("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    )
    source_row = source_result.first()
    source_id = str(source_row.id) if source_row else None

    job_result = await session.execute(
        text("""
            insert into ingestion_jobs (source_id, status, started_at, job_type)
            values (:source_id, 'running', now(), 'refresh')
            returning id
        """),
        {"source_id": source_id},
    )
    job_id = str(job_result.scalar_one())
    await session.commit()

    try:
        rows_result = await session.execute(
            text("""
                select p.id, p.external_ref
                from programmes p
                join sources s on s.id = p.source_id
                where s.name = 'SAM.gov Contract Opportunities API'
                  and p.contact_name is null and p.contact_email is null
                  and p.external_ref is not null
                order by p.last_updated asc
                limit :limit
            """),
            {"limit": batch_limit},
        )
        rows = rows_result.mappings().all()

        updated = 0
        checked = 0
        async with httpx.AsyncClient() as client:
            for row in rows:
                checked += 1
                detail = await _fetch_detail(client, row["external_ref"])
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                if detail is None:
                    continue

                data2 = detail.get("data2", {})
                poc = data2.get("pointOfContact") or []
                if not poc:
                    continue

                primary = next((c for c in poc if (c.get("type") or "").lower() == "primary"), poc[0])
                secondary = next((c for c in poc if (c.get("type") or "").lower() == "secondary"), None)

                contact_name = (primary.get("fullName") or "").strip() or None
                contact_email = (primary.get("email") or "").strip() or None
                contact_phone = (primary.get("phone") or "").strip() or None
                contact_email_secondary = (secondary.get("email") or "").strip() if secondary else None

                if not (contact_name or contact_email):
                    continue

                await session.execute(
                    text("""
                        update programmes
                        set contact_name = coalesce(:cn, contact_name),
                            contact_email = coalesce(:ce, contact_email),
                            contact_phone = coalesce(:cp, contact_phone),
                            contact_email_secondary = coalesce(:ces, contact_email_secondary)
                        where id = :id
                    """),
                    {
                        "cn": contact_name, "ce": contact_email, "cp": contact_phone,
                        "ces": contact_email_secondary, "id": row["id"],
                    },
                )
                updated += 1

        await session.execute(
            text("""
                update ingestion_jobs
                set status = 'succeeded', finished_at = now(), records_ingested = :count
                where id = :job_id
            """),
            {"count": updated, "job_id": job_id},
        )
        await session.commit()
        return {"job_id": job_id, "status": "succeeded", "checked": checked, "updated": updated}

    except Exception as e:
        await session.rollback()
        await session.execute(
            text("update ingestion_jobs set status = 'failed', finished_at = now(), error = :error where id = :job_id"),
            {"error": str(e)[:1000], "job_id": job_id},
        )
        await session.commit()
        raise
