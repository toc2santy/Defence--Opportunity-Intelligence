"""
Market Intelligence — the real data behind the Global Markets page.

Replaces what was previously six hand-picked, invented country scores
(India/Middle East/Southeast Asia/Europe/Africa/Americas with a
0-100 "attractiveness score" nobody could trace to a source). This
module aggregates the SAME programmes every other Intelligence engine
already reads — no new ingestion, no new trust/evidence questions —
grouped by `programmes.country` instead of by buying organisation
(app/customer_intelligence.py) or procurement stage
(app/procurement_intelligence.py).

Deliberately does NOT invent an "attractiveness score", "entry
complexity" or "partner ecosystem" rating — those were exactly the
fabricated numbers being replaced. What IS shown (programme count,
stage funnel, capability breakdown, buyer count, contributing
sources) is either a direct count or a capability resolved the same
way Customer/OEM Intelligence already do — nothing here is invented.
"""

from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import build_code_index, resolve_capabilities
from app.procurement_intelligence import PROGRAMME_STAGES


async def _load_country_programmes(session: AsyncSession):
    result = await session.execute(text("""
        select p.id, p.name, p.stage, p.naics_code, p.response_deadline, p.country,
               p.organization_id, s.name as source_name
        from programmes p
        left join sources s on s.id = p.source_id
        where p.country is not null
    """))
    return result.all()


async def list_markets(session: AsyncSession) -> list[dict]:
    """
    One row per country that has at least one ingested programme,
    ranked by programme count — the most active market for real
    procurement activity first, replacing the old invented ranking.
    """
    rows = await _load_country_programmes(session)
    index = await build_code_index(session)

    by_country: dict[str, dict] = {}
    for row in rows:
        c = by_country.setdefault(row.country, {
            "country": row.country,
            "programme_count": 0,
            "buyer_ids": set(),
            "sources": set(),
            "stage_counts": Counter(),
            "capability_counts": Counter(),
        })
        c["programme_count"] += 1
        if row.organization_id:
            c["buyer_ids"].add(str(row.organization_id))
        if row.source_name:
            c["sources"].add(row.source_name)
        if row.stage:
            c["stage_counts"][row.stage] += 1
        for resolved in resolve_capabilities(row.naics_code, index):
            c["capability_counts"][resolved] += 1

    markets = []
    for c in by_country.values():
        markets.append({
            "country": c["country"],
            "programme_count": c["programme_count"],
            "buyer_count": len(c["buyer_ids"]),
            "sources": sorted(c["sources"]),
            "stage_breakdown": [
                {"stage": stage, "count": c["stage_counts"].get(stage, 0)}
                for stage in PROGRAMME_STAGES if c["stage_counts"].get(stage, 0) > 0
            ],
            "top_capabilities": [
                {"capability_code": code, "label": label, "count": count}
                for (code, label), count in c["capability_counts"].most_common(5)
            ],
        })

    markets.sort(key=lambda m: m["programme_count"], reverse=True)
    return markets
