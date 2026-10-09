"""
Automatic data-health checks (2026-10, migration 055).

Tests prove the code does what its author expected; they cannot tell whether the
stored tenders still mean what the source says. This module checks the stored
data against its own invariants, per source, with no human step:

  awarded_but_open       a tender stored as open that already has an award recorded
  impossible_deadline    a closing date before 2000 or more than 5 years ahead
  malformed_link         a stored link that is not a plain http(s) URL
  open_without_deadline  open tenders with no closing date (the dashboard cannot
                         tell they have closed)
  open_without_action    open tenders with neither a link nor a contact
  freshness              time since the source's last successful ingestion
  row_count              total stored per source; a sudden drop means rows vanished
  reconciliation_freshness  has the weekly check against the source run recently
  (source_reconciliation rows are written by app/source_reconcile.py)

Runs after every scheduled ingestion (for that source only), daily for all
sources, and on demand. An ERROR that is new since the previous run sends an
alert (ALERT_EMAIL; logged when unset). Never raises into the caller's job.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OPEN_STAGES = ("early_concept", "requirement_defined", "rfi_issued", "rfp_issued")

NO_DEADLINE_WARN_PCT = 30
NO_ACTION_WARN_PCT = 20
FRESH_WARN_DAYS = 3
FRESH_ERROR_DAYS = 10
ROW_DROP_ERROR_PCT = 20
RECONCILE_WARN_DAYS = 10
RETENTION_DAYS = 180


def severity_for_percent(pct: float, warn_at: float) -> str:
    return "warn" if pct >= warn_at else "ok"


def freshness_severity(age: Optional[timedelta]) -> str:
    if age is None:
        return "warn"
    if age > timedelta(days=FRESH_ERROR_DAYS):
        return "error"
    if age > timedelta(days=FRESH_WARN_DAYS):
        return "warn"
    return "ok"


def row_drop_severity(previous: Optional[int], current: int) -> str:
    if not previous:
        return "ok"
    drop = (previous - current) / previous * 100
    return "error" if drop >= ROW_DROP_ERROR_PCT else "ok"


def _result(source, code, severity, affected, total, message, detail=None):
    return {"source_name": source, "check_code": code, "severity": severity,
            "affected": affected, "total": total, "message": message, "detail": detail or {}}


async def _per_source_counts(session, source_filter):
    sql = f"""
        select s.name as source,
               count(*) as total,
               count(*) filter (where p.stage = any(:open)) as open_rows,
               count(distinct p.id) filter (where p.stage = any(:open)
                    and exists (select 1 from contract_awards a where a.programme_id = p.id)) as awarded_but_open,
               count(*) filter (where p.response_deadline ~ '^[0-9]{{4}}-[0-9]{{2}}'
                    and (left(p.response_deadline, 4)::int < 2000
                         or left(p.response_deadline, 4)::int > extract(year from now())::int + 5)) as impossible_deadline,
               count(*) filter (where p.ui_link is not null and p.ui_link !~* '^https?://[^ ]+$') as malformed_link,
               count(*) filter (where p.stage = any(:open) and (p.response_deadline is null or p.response_deadline = '')) as open_no_deadline,
               count(*) filter (where p.stage = any(:open) and p.ui_link is null
                    and p.contact_name is null and p.contact_email is null) as open_no_action
        from programmes p join sources s on s.id = p.source_id
        {'where s.name = :src' if source_filter else ''}
        group by s.name
    """
    params = {"open": list(OPEN_STAGES)}
    if source_filter:
        params["src"] = source_filter
    return (await session.execute(text(sql), params)).mappings().all()


async def _examples(session, source, where_sql, limit=3):
    rows = (await session.execute(text(f"""
        select p.external_ref, left(p.name, 80) as name, p.stage from programmes p
        join sources s on s.id = p.source_id where s.name = :src and {where_sql} limit {limit}
    """), {"src": source, "open": list(OPEN_STAGES)})).mappings().all()
    return [dict(r) for r in rows]


async def collect_checks(session: AsyncSession, only_source: Optional[str] = None) -> list[dict]:
    results = []
    now = datetime.now(timezone.utc)

    for row in await _per_source_counts(session, only_source):
        src, total, open_rows = row["source"], row["total"], row["open_rows"]

        n = row["awarded_but_open"]
        results.append(_result(
            src, "awarded_but_open", "error" if n else "ok", n, open_rows,
            f"{n} of {open_rows} open tenders already have an award recorded — their stage is wrong." if n
            else "No open tender has an award recorded.",
            {"examples": await _examples(session, src,
                "p.stage = any(:open) and exists (select 1 from contract_awards a where a.programme_id = p.id)")} if n else {}))

        for code, key, label in (("impossible_deadline", "impossible_deadline", "closing dates before 2000 or over 5 years ahead"),
                                 ("malformed_link", "malformed_link", "stored links that are not plain http(s) URLs")):
            n = row[key]
            results.append(_result(src, code, "error" if n else "ok", n, total,
                                   f"{n} {label}." if n else f"No {label}."))

        for code, key, warn_at, label in (
            ("open_without_deadline", "open_no_deadline", NO_DEADLINE_WARN_PCT,
             "open tenders have no closing date, so the dashboard cannot tell if they have closed"),
            ("open_without_action", "open_no_action", NO_ACTION_WARN_PCT,
             "open tenders have neither an apply link nor a contact"),
        ):
            n = row[key]
            pct = (n / open_rows * 100) if open_rows else 0.0
            results.append(_result(src, code, severity_for_percent(pct, warn_at), n, open_rows,
                                   f"{n} of {open_rows} ({pct:.0f}%) {label}." if open_rows else "No open tenders.",
                                   {"percent": round(pct, 1)}))

        prev = (await session.execute(text("""
            select total from data_health_results where source_name = :s and check_code = 'row_count'
            order by checked_at desc limit 1"""), {"s": src})).scalar()
        results.append(_result(src, "row_count", row_drop_severity(prev, total), 0, total,
                               f"{total} tenders stored" + (f" (previous check: {prev})." if prev is not None else "."),
                               {"previous": prev}))

    # freshness: last successful ingestion per source
    fresh_sql = """
        select s.name as source, max(j.finished_at) as last_ok
        from sources s
        join programmes pr on pr.source_id = s.id
        left join ingestion_jobs j on j.source_id = s.id and j.status = 'succeeded' and coalesce(j.job_type, 'ingestion') = 'ingestion'
        {w}
        group by s.name
    """.format(w="where s.name = :src" if only_source else "")
    params = {"src": only_source} if only_source else {}
    for r in (await session.execute(text(fresh_sql), params)).mappings().all():
        age = (now - r["last_ok"]) if r["last_ok"] else None
        sev = freshness_severity(age)
        msg = ("Never ingested successfully." if age is None
               else f"Last successful ingestion {age.days}d {age.seconds // 3600}h ago.")
        results.append(_result(r["source"], "freshness", sev, 0, 0, msg,
                               {"last_success": r["last_ok"].isoformat() if r["last_ok"] else None}))

    # the weekly check against the source itself must keep happening
    if not only_source:
        from app.source_reconcile import ADAPTERS
        for name in ADAPTERS:
            last = (await session.execute(text("""
                select max(checked_at) from data_health_results
                where source_name = :s and check_code = 'source_reconciliation'"""), {"s": name})).scalar()
            age = (now - last) if last else None
            sev = "ok" if age is not None and age <= timedelta(days=RECONCILE_WARN_DAYS) else "warn"
            results.append(_result(name, "reconciliation_freshness", sev, 0, 0,
                                   "Compared with the source itself " + (f"{age.days}d ago." if age is not None else "never yet.")))
    return results


async def run_data_health(session: AsyncSession, only_source: Optional[str] = None, trigger: str = "scheduler") -> dict:
    run_id = str(uuid.uuid4())
    results = await collect_checks(session, only_source)

    previous_errors = {
        (r.source_name, r.check_code) for r in (await session.execute(text("""
            select distinct on (source_name, check_code) source_name, check_code, severity
            from data_health_results order by source_name, check_code, checked_at desc"""))).all()
        if r.severity == "error"
    }

    for r in results:
        await session.execute(text("""
            insert into data_health_results (run_id, trigger, source_name, check_code, severity, affected, total, message, detail)
            values (:run, :trig, :src, :code, :sev, :aff, :tot, :msg, CAST(:d AS jsonb))"""),
            {"run": run_id, "trig": trigger, "src": r["source_name"], "code": r["check_code"], "sev": r["severity"],
             "aff": r["affected"], "tot": r["total"], "msg": r["message"], "d": json.dumps(r["detail"], default=str)})

    await session.execute(text("delete from data_health_results where checked_at < now() - make_interval(days => :d)"),
                          {"d": RETENTION_DAYS})
    await session.commit()

    new_errors = [r for r in results if r["severity"] == "error" and (r["source_name"], r["check_code"]) not in previous_errors]
    if new_errors:
        _alert_new_errors(new_errors)

    errors = sum(1 for r in results if r["severity"] == "error")
    warns = sum(1 for r in results if r["severity"] == "warn")
    return {"run_id": run_id, "status": "error" if errors else ("warn" if warns else "ok"),
            "errors": errors, "warnings": warns, "checks": len(results), "new_errors": len(new_errors)}


def _alert_new_errors(new_errors: list[dict]) -> None:
    try:
        from app.backup import _alert
        body = "New data-health errors:\n\n" + "\n".join(
            f"- [{r['source_name']}] {r['check_code']}: {r['message']}" for r in new_errors)
        _alert(f"Data health: {len(new_errors)} new error(s)", body)
    except Exception as e:                       # alerting must never break the check
        print(f"[data health] alert failed: {e}")


async def latest_results(session: AsyncSession) -> dict:
    rows = (await session.execute(text("""
        select distinct on (source_name, check_code)
               source_name, check_code, severity, affected, total, message, detail, checked_at
        from data_health_results order by source_name, check_code, checked_at desc"""))).mappings().all()
    by_source: dict = {}
    for r in rows:
        by_source.setdefault(r["source_name"] or "(all)", []).append({
            "check": r["check_code"], "severity": r["severity"], "affected": r["affected"], "total": r["total"],
            "message": r["message"], "detail": r["detail"], "checked_at": r["checked_at"].isoformat()})
    order = {"error": 0, "warn": 1, "ok": 2}
    summary = {}
    for src, checks in by_source.items():
        checks.sort(key=lambda c: (order[c["severity"]], c["check"]))
        summary[src] = checks[0]["severity"]
    return {"overall": "error" if "error" in summary.values() else ("warn" if "warn" in summary.values() else "ok"),
            "sources": [{"source": s, "status": summary[s], "checks": by_source[s]} for s in sorted(by_source, key=lambda x: (order[summary[x]], x))]}
