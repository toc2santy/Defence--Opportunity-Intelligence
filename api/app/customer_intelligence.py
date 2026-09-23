"""
Customer Intelligence — Phase 4 of the original roadmap, deliberately
deferred (see README) in favor of Opportunity Intelligence, now built
because that trade-off has been revisited.

Pure aggregation over data already ingested by every source — no new
ingestion, no new API calls, no new trust/evidence questions. Every
programme already has a buying organization (`programmes.
organization_id`, `org_type = 'government_body'`); this module
answers "who are these buyers, and what do they buy" by grouping
programmes per organization and resolving each one's classification
code to a capability via app/capability_resolver.py.

Small dataset (2,000-ish programmes at the time this was built), so
this deliberately aggregates in Python after one bulk fetch rather
than building a more complex SQL aggregation — simpler to read, and
fast enough that a smarter query isn't worth the complexity yet. If
the programmes table grows by an order of magnitude, revisit that
trade-off first.
"""

from collections import Counter, defaultdict
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import build_code_index, resolve_capabilities


async def _load_buyer_programmes(session: AsyncSession):
    result = await session.execute(text("""
        select p.id, p.name, p.stage, p.naics_code, p.response_deadline,
               p.organization_id, org.name as organization_name, org.country,
               s.name as source_name
        from programmes p
        join organizations org on org.id = p.organization_id
        left join sources s on s.id = p.source_id
        where org.org_type = 'government_body'
    """))
    return result.all()


async def list_customers(session: AsyncSession) -> list[dict]:
    """
    One row per buying organization, sorted by how many programmes
    they've published — the most active buyers first, since that's
    the useful ordering for "who should I pay attention to."
    """
    rows = await _load_buyer_programmes(session)
    index = await build_code_index(session)

    by_org: dict[str, dict] = {}
    for row in rows:
        org = by_org.setdefault(str(row.organization_id), {
            "organization_id": str(row.organization_id),
            "organization_name": row.organization_name,
            "country": row.country,
            "programme_count": 0,
            "sources": set(),
            "capability_counts": Counter(),
        })
        org["programme_count"] += 1
        if row.source_name:
            org["sources"].add(row.source_name)
        for resolved in resolve_capabilities(row.naics_code, index):
            org["capability_counts"][resolved] += 1

    customers = []
    for org in by_org.values():
        top_capabilities = [
            {"capability_code": code, "label": label, "count": count}
            for (code, label), count in org["capability_counts"].most_common(3)
        ]
        customers.append({
            "organization_id": org["organization_id"],
            "organization_name": org["organization_name"],
            "country": org["country"],
            "programme_count": org["programme_count"],
            "sources": sorted(org["sources"]),
            "top_capabilities": top_capabilities,
        })

    customers.sort(key=lambda c: c["programme_count"], reverse=True)
    return customers


async def get_customer_detail(session: AsyncSession, organization_id: str) -> Optional[dict]:
    """
    Full programme list for one buyer, each tagged with its resolved
    capability where one exists — the drill-down behind a row in
    list_customers.
    """
    rows = await _load_buyer_programmes(session)
    matching = [r for r in rows if str(r.organization_id) == organization_id]
    if not matching:
        return None

    index = await build_code_index(session)
    capability_counts: Counter = Counter()
    programmes = []
    for row in matching:
        resolved_all = resolve_capabilities(row.naics_code, index)
        for resolved in resolved_all:
            capability_counts[resolved] += 1
        programmes.append({
            "programme_id": str(row.id),
            "name": row.name,
            "stage": row.stage,
            "classification_code": row.naics_code,
            "capability_label": ", ".join(label for _, label in resolved_all) or None,
            "response_deadline": row.response_deadline,
            "source_name": row.source_name,
        })

    programmes.sort(key=lambda p: p["response_deadline"] or "", reverse=True)

    first = matching[0]
    return {
        "organization_id": organization_id,
        "organization_name": first.organization_name,
        "country": first.country,
        "programme_count": len(matching),
        "capability_breakdown": [
            {"capability_code": code, "label": label, "count": count}
            for (code, label), count in capability_counts.most_common()
        ],
        "programmes": programmes,
    }
