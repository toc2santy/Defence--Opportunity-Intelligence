"""
Source reconciliation (2026-10): compare what we STORED about a tender with what
the source says NOW, and correct the fields where the source is authoritative.

This is the automatic version of "open the tender on the portal and look". It
exists because the SECOP II and Paraguay stage bugs could not be seen from our
own data: every row looked fine until it was compared with the source's record.
Run weekly for every source that has an adapter below, and on demand.

Scope, deliberately small: only `stage` and `response_deadline` are corrected.
Name, buyer, code, link and contacts are never touched, nothing is deleted, and
a procedure the source now lists as cancelled/void is only COUNTED (programmes
has no "called off" stage; removing rows is a human decision).

Sources without an adapter (SAM.gov, TED, CanadaBuys, UK, AusTender, CPPP,
South Africa, ProZorro) have no per-record lookup implemented here; they are
reported as `not_reconcilable` instead of being silently skipped.
"""

import asyncio
import json
import uuid
from typing import Awaitable, Callable, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MISMATCH_ERROR_RATE = 0.05    # >5% of checked rows had a wrong stage -> error
IDS_PER_REQUEST = 40


def plan_changes(stored: dict, fresh: Optional[dict]) -> dict:
    """Pure decision for ONE stored row, given the source's normalised fresh record (or None)."""
    if fresh is None:
        return {"outcome": "not_found"}
    if fresh["stage"] is None:
        return {"outcome": "now_cancelled_or_void"}
    changes = {}
    if fresh["stage"] != stored["stage"]:
        changes["stage"] = fresh["stage"]
    if fresh["response_deadline"] and fresh["response_deadline"] != stored["response_deadline"]:
        changes["response_deadline"] = fresh["response_deadline"]
    return {"outcome": "changed" if changes else "unchanged", "changes": changes}


# ---- adapters: refs -> {ref: {"stage", "response_deadline"} | missing} ----
Fetcher = Callable[[httpx.AsyncClient, list[str]], Awaitable[dict]]


async def _secop_fetch(client: httpx.AsyncClient, refs: list[str]) -> dict:
    from app.colombia_normalize import normalize_row
    from app.colombia_refresh import _fetch_by_ids
    out = {}
    for raw in await _fetch_by_ids(client, refs):
        try:
            rec = normalize_row(raw)
        except (ValueError, AttributeError, TypeError):
            continue
        out[rec["external_ref"]] = {"stage": rec["stage"], "response_deadline": rec["response_deadline"]}
    return out


PARAGUAY_ATTEMPTS = 4
PARAGUAY_RETRY_BACKOFF_SECONDS = 2.0


async def _paraguay_get_record(client: httpx.AsyncClient, ref: str) -> Optional[dict]:
    """
    One DNCP record, with retries. This source is known to drop connections
    ("Server disconnected without sending a response") and to cut a 200 response off
    mid-JSON (see CLAUDE.md, DNCP Paraguay) — both are retried; a record that still
    fails after PARAGUAY_ATTEMPTS is skipped (reported as not returned) rather than
    aborting the whole reconciliation. A 404 means the record is gone.
    """
    from app.paraguay_ingestion import PARAGUAY_RECORD_URL
    last_error = None
    for attempt in range(1, PARAGUAY_ATTEMPTS + 1):
        try:
            resp = await client.get(f"{PARAGUAY_RECORD_URL}/{ref}", headers={"User-Agent": "defence-oi-ingestion/1.0"}, timeout=60.0)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
            last_error = e
            await asyncio.sleep(PARAGUAY_RETRY_BACKOFF_SECONDS * attempt)
    print(f"[source_reconcile] Paraguay {ref}: skipped after {PARAGUAY_ATTEMPTS} attempts ({type(last_error).__name__})")
    return None


async def _paraguay_fetch(client: httpx.AsyncClient, refs: list[str]) -> dict:
    from app.paraguay_normalize import _stage_for, response_deadline_from_tender
    out = {}
    for ref in refs:
        payload = await _paraguay_get_record(client, ref)
        await asyncio.sleep(0.3)
        records = (payload or {}).get("records") or []
        if not records:
            continue
        tender = (records[0].get("compiledRelease") or {}).get("tender") or {}
        out[ref] = {
            "stage": _stage_for(tender.get("statusDetails"), tender.get("status")),
            "response_deadline": response_deadline_from_tender(tender),
        }
    return out


ADAPTERS: dict[str, Fetcher] = {
    "SECOP II (Colombia Compra Eficiente)": _secop_fetch,
    "DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)": _paraguay_fetch,
}


async def reconcile_source(
    session: AsyncSession, source_name: str, *, sample: Optional[int] = None,
    dry_run: bool = False, record_job: bool = True, fetcher: Optional[Fetcher] = None,
) -> dict:
    fetch = fetcher or ADAPTERS.get(source_name)
    if fetch is None:
        return {"status": "not_reconcilable", "source": source_name}

    source = (await session.execute(text("select id from sources where name = :n"), {"n": source_name})).first()
    if source is None:
        return {"status": "skipped", "reason": "source not registered", "source": source_name}
    source_id = str(source.id)

    job_id = None
    if record_job and not dry_run:
        job_id = str((await session.execute(
            text("insert into ingestion_jobs (source_id, status, started_at, job_type) "
                 "values (:s, 'running', now(), 'refresh') returning id"), {"s": source_id},
        )).scalar_one())
        await session.commit()

    try:
        order = "random()" if sample else "last_updated asc"
        rows = (await session.execute(
            text(f"""
                select id, external_ref, stage, response_deadline from programmes
                where source_id = :s and external_ref is not null
                order by {order} limit :lim
            """), {"s": source_id, "lim": sample or 2000},
        )).mappings().all()

        fresh_by_ref: dict = {}
        async with httpx.AsyncClient() as client:
            for i in range(0, len(rows), IDS_PER_REQUEST):
                chunk = [r["external_ref"] for r in rows[i:i + IDS_PER_REQUEST]]
                fresh_by_ref.update(await fetch(client, chunk))
                await asyncio.sleep(0.3)

        counts = {"checked": len(rows), "unchanged": 0, "stage_changed": 0, "deadline_set": 0,
                  "not_found": 0, "now_cancelled_or_void": 0}
        moves: dict[str, int] = {}
        examples = []
        for r in rows:
            plan = plan_changes({"stage": r["stage"], "response_deadline": r["response_deadline"]}, fresh_by_ref.get(r["external_ref"]))
            if plan["outcome"] != "changed":
                counts[plan["outcome"]] += 1
                continue
            ch = plan["changes"]
            if "stage" in ch:
                counts["stage_changed"] += 1
                key = f"{r['stage']} -> {ch['stage']}"
                moves[key] = moves.get(key, 0) + 1
                if len(examples) < 5:
                    examples.append({"ref": r["external_ref"], "stored": r["stage"], "source_says": ch["stage"]})
            if "response_deadline" in ch:
                counts["deadline_set"] += 1
            if not dry_run:
                await session.execute(
                    text("""update programmes set stage = coalesce(:stage, stage),
                            response_deadline = coalesce(:deadline, response_deadline), last_updated = now()
                            where id = :id"""),
                    {"stage": ch.get("stage"), "deadline": ch.get("response_deadline"), "id": r["id"]},
                )

        found = counts["checked"] - counts["not_found"]
        rate = (counts["stage_changed"] / found) if found else 0.0
        result = {"source": source_name, **counts, "stage_moves": moves, "mismatch_rate": round(rate, 4),
                  "dry_run": dry_run, "sampled": bool(sample)}

        if dry_run:
            return {"status": "dry_run", **result}

        sev = "error" if rate > MISMATCH_ERROR_RATE else ("warn" if counts["stage_changed"] else "ok")
        await _record_result(session, source_name, sev, counts, rate, moves, examples, sampled=bool(sample))
        if job_id:
            await session.execute(
                text("update ingestion_jobs set status='succeeded', finished_at=now(), records_ingested=:n where id=:j"),
                {"n": counts["stage_changed"] + counts["deadline_set"], "j": job_id},
            )
        await session.commit()
        return {"status": "succeeded", "job_id": job_id, **result}
    except Exception as e:
        await session.rollback()
        if job_id:
            await session.execute(text("update ingestion_jobs set status='failed', finished_at=now(), error=:e where id=:j"),
                                  {"e": str(e)[:1000], "j": job_id})
            await session.commit()
        raise


async def _record_result(session, source_name, severity, counts, rate, moves, examples, *, sampled):
    msg = (f"Compared {counts['checked']} stored tenders with the source: {counts['stage_changed']} had a wrong stage "
           f"({rate:.0%}), {counts['deadline_set']} gained a closing date"
           + (f", {counts['not_found']} no longer returned" if counts["not_found"] else "")
           + (f", {counts['now_cancelled_or_void']} now cancelled/void" if counts["now_cancelled_or_void"] else "")
           + (" (random sample)" if sampled else "") + ". Corrected automatically.")
    await session.execute(
        text("""insert into data_health_results (run_id, trigger, source_name, check_code, severity, affected, total, message, detail)
                values (:run, 'reconciliation', :src, 'source_reconciliation', :sev, :aff, :tot, :msg, CAST(:d AS jsonb))"""),
        {"run": str(uuid.uuid4()), "src": source_name, "sev": severity, "aff": counts["stage_changed"],
         "tot": counts["checked"], "msg": msg,
         "d": json.dumps({"stage_moves": moves, "examples": examples, "mismatch_rate": rate, **counts})},
    )


async def reconcile_all(session: AsyncSession, *, dry_run: bool = False) -> dict:
    out = {}
    for name in ADAPTERS:
        try:
            out[name] = await reconcile_source(session, name, dry_run=dry_run)
        except Exception as e:           # one source's outage must not stop the others
            out[name] = {"status": "failed", "error": f"{type(e).__name__}: {str(e)[:250]}"}
            if not dry_run:
                await _record_failure(session, name, out[name]["error"])
    return out


async def _record_failure(session: AsyncSession, source_name: str, error: str) -> None:
    """A reconciliation that could not run must show up in data health, not only in a log line."""
    await session.execute(
        text("""insert into data_health_results (run_id, trigger, source_name, check_code, severity, affected, total, message, detail)
                values (:run, 'reconciliation', :src, 'source_reconciliation', 'warn', 0, 0, :msg, CAST(:d AS jsonb))"""),
        {"run": str(uuid.uuid4()), "src": source_name,
         "msg": f"Could not complete the comparison with the source ({error}). Nothing was changed; it is retried next week or on demand.",
         "d": json.dumps({"error": error})},
    )
    await session.commit()
