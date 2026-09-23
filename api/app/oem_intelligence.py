"""
OEM Intelligence — Phase 4 of the original roadmap, alongside
Customer Intelligence, built from real contract_awards data (see
db/migrations/016 and app/ingestion_common.get_or_create_oem_
organization / record_contract_award).

Structurally the mirror image of app/customer_intelligence.py: that
module aggregates by BUYER organization across every programme;
this one aggregates by WINNER organization across every contract
award. Reuses the same app/capability_resolver.py — a winner's
"top capabilities" is resolved from the classification codes of the
programmes they won, exactly as a buyer's is resolved from the
programmes they published.
"""

from collections import Counter
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import build_code_index, resolve_capabilities


async def load_all_awards(session: AsyncSession):
    result = await session.execute(text("""
        select ca.id as award_id, ca.winner_organization_id,
               org.name as winner_name, org.country as winner_country,
               p.id as programme_id, p.name as programme_name, p.naics_code,
               p.response_deadline, p.country as programme_country,
               p.organization_id as buyer_organization_id,
               buyer.name as buyer_name,
               s.name as source_name
        from contract_awards ca
        join organizations org on org.id = ca.winner_organization_id
        join programmes p on p.id = ca.programme_id
        left join organizations buyer on buyer.id = p.organization_id
        left join sources s on s.id = ca.source_id
    """))
    return result.all()


async def list_oems(session: AsyncSession) -> list[dict]:
    """
    One row per winning organization, sorted by award count — the
    most active, most-winning suppliers first.
    """
    rows = await load_all_awards(session)
    index = await build_code_index(session)

    by_org: dict[str, dict] = {}
    for row in rows:
        org = by_org.setdefault(str(row.winner_organization_id), {
            "organization_id": str(row.winner_organization_id),
            "organization_name": row.winner_name,
            "country": row.winner_country,
            "award_count": 0,
            "countries_won_in": set(),
            "sources": set(),
            "capability_counts": Counter(),
        })
        org["award_count"] += 1
        if row.programme_country:
            org["countries_won_in"].add(row.programme_country)
        if row.source_name:
            org["sources"].add(row.source_name)
        for resolved in resolve_capabilities(row.naics_code, index):
            org["capability_counts"][resolved] += 1

    oems = []
    for org in by_org.values():
        top_capabilities = [
            {"capability_code": code, "label": label, "count": count}
            for (code, label), count in org["capability_counts"].most_common(3)
        ]
        oems.append({
            "organization_id": org["organization_id"],
            "organization_name": org["organization_name"],
            "country": org["country"],
            "award_count": org["award_count"],
            "countries_won_in": sorted(org["countries_won_in"]),
            "sources": sorted(org["sources"]),
            "top_capabilities": top_capabilities,
        })

    oems.sort(key=lambda o: o["award_count"], reverse=True)
    return oems


async def get_oem_detail(session: AsyncSession, organization_id: str) -> Optional[dict]:
    """Every award for one OEM, tagged with its resolved capability."""
    rows = await load_all_awards(session)
    matching = [r for r in rows if str(r.winner_organization_id) == organization_id]
    if not matching:
        return None

    index = await build_code_index(session)
    capability_counts: Counter = Counter()
    awards = []
    for row in matching:
        resolved_all = resolve_capabilities(row.naics_code, index)
        for resolved in resolved_all:
            capability_counts[resolved] += 1
        awards.append({
            "award_id": str(row.award_id),
            "programme_id": str(row.programme_id),
            "programme_name": row.programme_name,
            "capability_label": ", ".join(label for _, label in resolved_all) or None,
            "buyer_name": row.buyer_name,
            "country": row.programme_country,
            "response_deadline": row.response_deadline,
            "source_name": row.source_name,
        })

    awards.sort(key=lambda a: a["response_deadline"] or "", reverse=True)

    first = matching[0]
    return {
        "organization_id": organization_id,
        "organization_name": first.winner_name,
        "country": first.winner_country,
        "award_count": len(matching),
        "capability_breakdown": [
            {"capability_code": code, "label": label, "count": count}
            for (code, label), count in capability_counts.most_common()
        ],
        "awards": awards,
    }
