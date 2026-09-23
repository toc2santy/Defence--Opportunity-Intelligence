"""
Procurement Intelligence — the original roadmap's Phase 4. Already
"real" in a narrow sense before this (stage/deadline data has been
folded into every source's ingestion since SAM.gov was first built —
see README), but never surfaced as its own view. This module is
that view: where every ingested programme sits in the government's
own procurement lifecycle, aggregated by stage/source/country.

Deliberately distinct from `opportunities.stage` (app/main.py's
OPPORTUNITY_STAGES) — that is the TENANT'S own sales pipeline for a
specific product against a specific programme. This is the
GOVERNMENT'S procurement lifecycle for the programme itself, shared
reference data with no tenant scoping, exactly like Customer and OEM
Intelligence.
"""

from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.test_fixture_filter import not_a_test_fixture

# programmes.stage's real check-constraint values (db/schema.sql) —
# listed here, not inferred from the data, so a stage with zero
# programmes right now still appears in the funnel at count 0 rather
# than silently disappearing from the summary.
PROGRAMME_STAGES = (
    "early_concept", "requirement_defined", "rfi_issued",
    "rfp_issued", "contract_awarded", "in_service",
)


async def get_procurement_funnel(session: AsyncSession) -> dict:
    # A real bug, found live (2026-09): this query used to have no
    # not_a_test_fixture() filter at all, on the reasoning (see
    # main.py's own not_a_test_fixture docstring) that fixture
    # pollution here was "harmless for the authenticated Procurement
    # Intelligence tab". That stopped being true the moment the
    # Presentation page started reading total_programmes from THIS
    # function as a live, customer-facing number during sales demos —
    # a user directly noticed Home and Presentation showing different
    # "Live Tenders Tracked" counts, traced to 658 "Test Fixture:..."
    # rows (out of ~10,558 total) counted here but excluded from
    # /public/platform-stats. There's no legitimate reason an
    # authenticated analyst reading the real government procurement
    # funnel would want synthetic test rows in it either — filtering
    # them here fixes both callers at once, not just Presentation.
    rows = await session.execute(text(f"""
        select p.stage, p.country, s.name as source_name
        from programmes p
        left join sources s on s.id = p.source_id
        where {not_a_test_fixture('p')}
    """))
    rows = rows.all()

    stage_counts = Counter(r.stage for r in rows)
    by_source: dict[str, Counter] = {}
    for r in rows:
        source = r.source_name or "Unknown"
        by_source.setdefault(source, Counter())[r.stage] += 1

    by_country = Counter(r.country for r in rows if r.country)

    return {
        "total_programmes": len(rows),
        # The TRUE distinct-country count. top_countries below is
        # capped at 15 for display, so counting its length gives a
        # silently wrong answer — a mistake made once already while
        # wiring the Presentation page's KPI.
        "country_count": len(by_country),
        "funnel": [
            {"stage": stage, "count": stage_counts.get(stage, 0)}
            for stage in PROGRAMME_STAGES
        ],
        "by_source": [
            {
                "source_name": source,
                "total": sum(counts.values()),
                "funnel": [{"stage": s, "count": counts.get(s, 0)} for s in PROGRAMME_STAGES],
            }
            for source, counts in sorted(by_source.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
        ],
        "top_countries": [
            {"country": country, "count": count}
            for country, count in by_country.most_common(15)
        ],
    }
