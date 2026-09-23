"""
Sector Coverage — how many ingested programmes (shared, platform-wide
data) and how many of the CURRENT TENANT's own opportunities fall
into each of the platform's capability sectors.

Reuses app/capability_resolver.py's code -> capability resolution —
the exact same mechanism Customer/OEM Intelligence already use — so a
programme's sector here can never drift from what those modules would
say about the same programme; this is not a second, parallel
definition of "what sector is this" that could quietly disagree with
the rest of the platform.

A programme's classification code can resolve to capabilities in MORE
THAN ONE sector (see capability_resolver's own docstring — this is
real, not a bug: CPV 35400000 deliberately maps to both LAND.SYSTEMS
and MANUFACTURING.DEFENCE), so a single programme can legitimately
count toward more than one sector's total. The sum of every sector's
programme_count is therefore expected to exceed the platform's real
total programme count — that is honest double-counting of a real
multi-sector fact, not an error to be de-duplicated away.

Same "aggregate in Python after one bulk fetch" choice
customer_intelligence.py already made at this data scale (~6,000
programmes) — simpler to read than a SQL aggregation that would have
to reimplement capability_resolver's own exact/prefix matching logic
a second time, and fast enough that the simpler approach isn't worth
avoiding yet.
"""

from collections import defaultdict
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import CodeIndex, build_code_index, resolve_capabilities


async def build_sector_index(session: AsyncSession) -> tuple[CodeIndex, dict[str, str]]:
    """
    The two shared building blocks (code->capability index,
    capability_code->sector map) — fetched ONCE and reused by both
    sector_coverage() below and main.py's opportunity-list sector
    tagging, rather than each rebuilding its own copy of the same two
    lookups from the same tables.
    """
    index = await build_code_index(session)
    cap_sector_result = await session.execute(text("select code, sector from capability_taxonomy"))
    cap_sector = {row.code: row.sector for row in cap_sector_result}
    return index, cap_sector


def resolve_programme_sectors(naics_code: Optional[str], index: CodeIndex, cap_sector: dict[str, str]) -> list[str]:
    """Pure — the sector names one programme's classification code resolves to, or [] if none."""
    resolved = resolve_capabilities(naics_code, index)
    return sorted({cap_sector[cc] for cc, _ in resolved if cc in cap_sector})


async def sector_coverage(session: AsyncSession) -> dict:
    """
    programme_count is shared/global — every tenant sees the same
    real ingested-data counts, since programmes carries no tenant
    scoping. opportunity_count is scoped to the CURRENT tenant only,
    via the RLS-enforced session this function is handed — opportunities
    IS a tenant-owned table (see CLAUDE.md), so this never needs to
    filter by tenant_id by hand; the database already refuses to
    return another tenant's rows.
    """
    index, cap_sector = await build_sector_index(session)
    all_sectors = sorted(set(cap_sector.values()))

    programmes_result = await session.execute(text("select id, naics_code from programmes"))
    # programme_id -> set of sector names it resolves to. Only
    # programmes that resolve to at least one sector are kept — an
    # unmapped/unrecognised classification code is a real, expected
    # outcome (capability_resolver's own docstring), not something to
    # force into a sector it doesn't actually belong to.
    programme_sectors: dict[str, set[str]] = {}
    for row in programmes_result:
        sectors = set(resolve_programme_sectors(row.naics_code, index, cap_sector))
        if sectors:
            programme_sectors[str(row.id)] = sectors

    programme_counts: dict[str, int] = defaultdict(int)
    for sectors in programme_sectors.values():
        for sector in sectors:
            programme_counts[sector] += 1

    opp_result = await session.execute(
        text("select programme_id from opportunities where programme_id is not null")
    )
    opportunity_counts: dict[str, int] = defaultdict(int)
    # Real overlap, found live (2026-09) after a user saw the exact
    # same opportunity_count on three different sector cards (Radar,
    # Naval Systems, Sensors — all 994) and understandably read it as
    # a bug resurfacing, even though it's the correct, already-
    # verified outcome of migration 041: NAICS 334511 genuinely covers
    # radar, sonar AND general search/detection instruments in its
    # own official definition, so a tenant whose matched opportunities
    # are dominated by that one code will legitimately see the same
    # number on all three cards. The number was right; the UI gave no
    # way to tell "legitimate shared classification code" apart from
    # "duplicate bug" on sight. `shared_with` closes that gap — for
    # each sector, which OTHER sectors any of ITS opportunities also
    # count toward, computed from the exact same programme_sectors
    # multi-capability resolution already used for the counts
    # themselves, not a second, separate overlap heuristic.
    shared_with: dict[str, set[str]] = defaultdict(set)
    for row in opp_result:
        sectors = programme_sectors.get(str(row.programme_id), ())
        for sector in sectors:
            opportunity_counts[sector] += 1
        if len(sectors) > 1:
            for sector in sectors:
                shared_with[sector].update(s for s in sectors if s != sector)

    return {
        "sectors": [
            {
                "sector": sector,
                "programme_count": programme_counts.get(sector, 0),
                "opportunity_count": opportunity_counts.get(sector, 0),
                "shared_with": sorted(shared_with.get(sector, ())),
            }
            for sector in all_sectors
        ],
        "total_programmes_resolved": len(programme_sectors),
    }


async def capability_contribution(session: AsyncSession) -> dict:
    """
    Per-CAPABILITY, not per-sector — built for Taxonomy Admin (2026-09)
    after a user asked a fair question following migration 041 (which
    deleted 8 of NAICS 334511's 11 capability mappings): once a
    mapping is narrowed or removed, how do you actually SEE its real
    effect, instead of having to ask for a manual database check every
    time? Three real numbers per capability:

    - mapping_count: how many classification codes (NAICS + CPV +
      UNSPSC prefixes, summed) currently point to this capability at
      all — the taxonomy_*_mapping row count itself, the thing
      migration 041 actually edited.
    - programme_count: how many currently-ingested real programmes
      resolve to it through capability_resolver — the downstream
      effect of those mappings, same resolution logic Customer/OEM/
      Sector Coverage already use.
    - contract_award_count: how many real contract_awards exist
      against those programmes — genuine evidence of OEMs winning
      work in this capability area, not merely tenders existing.

    Same legitimate double-counting as sector_coverage(): one
    programme (and its awards) can resolve to more than one
    capability when a code is deliberately mapped to several (see
    capability_resolver's own docstring), so summing every
    capability's counts can exceed the platform's real totals — that
    is an honest multi-capability fact, not an error.
    """
    index, _ = await build_sector_index(session)

    mapping_counts_result = await session.execute(text("""
        select ct.code as capability_code, count(*) as mapping_count
        from (
            select capability_id from taxonomy_naics_mapping
            union all
            select capability_id from taxonomy_cpv_mapping
            union all
            select capability_id from taxonomy_unspsc_mapping
        ) m
        join capability_taxonomy ct on ct.id = m.capability_id
        group by ct.code
    """))
    mapping_counts = {row.capability_code: row.mapping_count for row in mapping_counts_result}

    programmes_result = await session.execute(text("select id, naics_code from programmes"))
    programme_capabilities: dict[str, set[str]] = {}
    for row in programmes_result:
        caps = {cc for cc, _ in resolve_capabilities(row.naics_code, index)}
        if caps:
            programme_capabilities[str(row.id)] = caps

    programme_counts: dict[str, int] = defaultdict(int)
    for caps in programme_capabilities.values():
        for cc in caps:
            programme_counts[cc] += 1

    awards_result = await session.execute(
        text("select programme_id from contract_awards where programme_id is not null")
    )
    award_counts: dict[str, int] = defaultdict(int)
    for row in awards_result:
        for cc in programme_capabilities.get(str(row.programme_id), ()):
            award_counts[cc] += 1

    all_capability_codes = set(mapping_counts) | set(programme_counts)
    return {
        cc: {
            "mapping_count": mapping_counts.get(cc, 0),
            "programme_count": programme_counts.get(cc, 0),
            "contract_award_count": award_counts.get(cc, 0),
        }
        for cc in all_capability_codes
    }
