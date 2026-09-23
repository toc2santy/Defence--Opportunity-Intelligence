"""
Competitor Intelligence — the original roadmap's Phase 4, and the
one genuinely TENANT-SCOPED intelligence module of the group (unlike
Customer/OEM/Procurement Intelligence, which are shared reference
data identical for every tenant).

A "competitor" is defined here as: a real company confirmed winning
a contract (app/oem_intelligence.py's data) in a capability THIS
TENANT has actually confirmed for one of their own products —
reusing the exact same confirmed-capability query
app/programme_matching.py uses for matching, so "your competitors"
and "what you'd match against" are answering from the same set of
facts about the tenant, not two different notions of "your
capabilities" drifting apart over time.
"""

from collections import Counter
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import build_code_index, resolve_capabilities
from app.oem_intelligence import load_all_awards


async def _load_tenant_capability_codes(session: AsyncSession) -> set[str]:
    """
    Same query shape as app/programme_matching._load_confirmed_capabilities
    — analyst-confirmed only, the same human-in-the-loop gate every
    other capability-driven feature in this project respects.
    """
    result = await session.execute(text("""
        select distinct pc.capability_id
        from product_capabilities pc
        where pc.classified_by = 'analyst'
    """))
    return {str(row.capability_id) for row in result}


async def list_competitors(session: AsyncSession) -> dict:
    """
    Returns an explicit `has_confirmed_capabilities` flag rather than
    just an empty list on the no-capabilities case — an empty
    competitor list because you have zero confirmed capabilities
    needs a different message ("go confirm a capability first") than
    an empty list because no OEM has yet won anything in a space you
    do operate in.
    """
    tenant_capability_ids = await _load_tenant_capability_codes(session)
    if not tenant_capability_ids:
        return {"has_confirmed_capabilities": False, "competitors": []}

    rows = await load_all_awards(session)
    index = await build_code_index(session)

    # Map capability_id -> (capability_code, label) once, so award
    # rows can be filtered by whether their resolved capability_id is
    # one the tenant holds, without re-querying per row.
    capability_id_by_code = {}
    id_lookup = await session.execute(text("select id, code from capability_taxonomy"))
    for row in id_lookup:
        capability_id_by_code[row.code] = str(row.id)

    by_org: dict[str, dict] = {}
    for row in rows:
        # A code can map to SEVERAL capabilities (see
        # capability_resolver's module docstring) — keep only the ones
        # this tenant actually holds, and count the award once if any
        # of them overlap rather than once per overlapping capability.
        shared = [
            (code, label)
            for code, label in resolve_capabilities(row.naics_code, index)
            if capability_id_by_code.get(code) in tenant_capability_ids
        ]
        if not shared:
            continue  # a real competitor in SOME space, just not one this tenant is in

        org = by_org.setdefault(str(row.winner_organization_id), {
            "organization_id": str(row.winner_organization_id),
            "organization_name": row.winner_name,
            "country": row.winner_country,
            "award_count_in_your_space": 0,
            "capability_counts": Counter(),
        })
        org["award_count_in_your_space"] += 1
        for pair in shared:
            org["capability_counts"][pair] += 1

    competitors = []
    for org in by_org.values():
        competitors.append({
            "organization_id": org["organization_id"],
            "organization_name": org["organization_name"],
            "country": org["country"],
            "award_count_in_your_space": org["award_count_in_your_space"],
            "shared_capabilities": [
                {"capability_code": code, "label": label, "count": count}
                for (code, label), count in org["capability_counts"].most_common()
            ],
        })
    competitors.sort(key=lambda c: c["award_count_in_your_space"], reverse=True)

    return {"has_confirmed_capabilities": True, "competitors": competitors}
