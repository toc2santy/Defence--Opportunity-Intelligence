"""
Partner Matching — the missing half of the original roadmap's engine
05 ("OEM & Partner Matching"). The OEM half (who wins contracts) is
app/oem_intelligence.py; this is the teaming half.

THE DEFINITION, stated plainly because it's the whole design:
a COMPETITOR (app/competitor_intelligence.py) is a company winning
in a capability YOU have confirmed. A PARTNER is a company winning
from THE SAME BUYERS you would target, but in a DIFFERENT capability
— complementary rather than competing. Same award data, opposite
filter on capability.

That definition is derivable entirely from data already ingested, and
every part of it is a published fact (who bought, who won, what
category) rather than an inference about intent or fit.

WHY THE FIT SCORE IS DELIBERATELY CRUDE: it is shared-buyer count,
nothing more. This project's honesty standard (see the README and
matching_scoring.py's history of self-correcting against real false
positives) makes a fabricated percentage worse than a plain count —
"has won from 3 of the same buyers as you, in capabilities you don't
cover" is verifiable; "87% partner fit" is not. If a weighted score
is ever added it needs a real basis, not a formula chosen because it
produces confident-looking numbers.
"""

from collections import Counter
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import build_code_index, resolve_capabilities
from app.oem_intelligence import load_all_awards


async def _tenant_capability_ids(session: AsyncSession) -> set[str]:
    result = await session.execute(text("""
        select distinct pc.capability_id
        from product_capabilities pc
        where pc.classified_by = 'analyst'
    """))
    return {str(row.capability_id) for row in result}


async def _tenant_target_buyer_ids(session: AsyncSession) -> set[str]:
    """
    The buyers behind this tenant's own matched opportunities — i.e.
    organisations they already have a real reason to care about,
    rather than every buyer in the database. `opportunities` is
    tenant-scoped by RLS, so this is automatically this tenant's own
    set without an explicit tenant_id filter (see CLAUDE.md on
    get_tenant_session).
    """
    result = await session.execute(text("""
        select distinct p.organization_id
        from opportunities o
        join programmes p on p.id = o.programme_id
        where p.organization_id is not null
    """))
    return {str(row.organization_id) for row in result}


async def list_partners(session: AsyncSession) -> dict:
    tenant_capability_ids = await _tenant_capability_ids(session)
    if not tenant_capability_ids:
        return {
            "has_confirmed_capabilities": False,
            "has_target_buyers": False,
            "partners": [],
        }

    target_buyer_ids = await _tenant_target_buyer_ids(session)
    if not target_buyer_ids:
        return {
            "has_confirmed_capabilities": True,
            "has_target_buyers": False,
            "partners": [],
        }

    rows = await load_all_awards(session)
    index = await build_code_index(session)

    capability_id_by_code = {}
    id_lookup = await session.execute(text("select id, code from capability_taxonomy"))
    for row in id_lookup:
        capability_id_by_code[row.code] = str(row.id)

    by_org: dict[str, dict] = {}
    for row in rows:
        buyer_id = str(row.buyer_organization_id) if row.buyer_organization_id else None
        if buyer_id not in target_buyer_ids:
            continue  # not a buyer this tenant is actually pursuing

        resolved = resolve_capabilities(row.naics_code, index)
        if not resolved:
            continue  # can't tell whether it complements or competes — excluded rather than guessed

        # A code can map to SEVERAL capabilities. If ANY of them is one
        # the tenant already holds, this is a competitor on that
        # overlap and must not be sold as a partner — the conservative
        # reading, since claiming a rival is a teaming candidate is the
        # more damaging error of the two.
        if any(capability_id_by_code.get(code) in tenant_capability_ids for code, _ in resolved):
            continue

        org = by_org.setdefault(str(row.winner_organization_id), {
            "organization_id": str(row.winner_organization_id),
            "organization_name": row.winner_name,
            "country": row.winner_country,
            "shared_buyers": set(),
            "complementary_capabilities": Counter(),
            "award_count": 0,
        })
        org["award_count"] += 1
        org["shared_buyers"].add(row.buyer_name or buyer_id)
        for pair in resolved:
            org["complementary_capabilities"][pair] += 1

    partners = []
    for org in by_org.values():
        partners.append({
            "organization_id": org["organization_id"],
            "organization_name": org["organization_name"],
            "country": org["country"],
            "award_count": org["award_count"],
            # The "fit score" — a plain count, deliberately. See the
            # module docstring on why this isn't a percentage.
            "shared_buyer_count": len(org["shared_buyers"]),
            "shared_buyers": sorted(org["shared_buyers"]),
            "complementary_capabilities": [
                {"capability_code": code, "label": label, "count": count}
                for (code, label), count in org["complementary_capabilities"].most_common()
            ],
        })

    partners.sort(key=lambda p: (p["shared_buyer_count"], p["award_count"]), reverse=True)
    return {
        "has_confirmed_capabilities": True,
        "has_target_buyers": True,
        "partners": partners,
    }
