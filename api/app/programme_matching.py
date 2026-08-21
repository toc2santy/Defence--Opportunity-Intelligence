"""
Phase 3 — Programme Intelligence matching.

Connects a tenant's CONFIRMED capabilities (analyst-reviewed, not
raw AI suggestions — this is the human-in-the-loop gate from
Phase 1 carried forward deliberately) to real programmes ingested
from any registered source, using the classification-code mapping
+ keyword re-score approach in app/matching_scoring.py.

GENERALIZED beyond NAICS: originally this only checked
taxonomy_naics_mapping, meaning UK-sourced (CPV-coded) programmes
were invisible to matching even after real UK data was flowing in —
a genuine gap, closed here by also checking taxonomy_cpv_mapping
and unioning both code sets before querying candidate programmes.
The external API response field is still called "naics_match" for
backward compatibility with the existing frontend and tests; it now
honestly means "matched via any classification code (NAICS or
CPV)", documented here rather than silently redefined.

This is the first code in the whole project that populates the
`opportunities` table for real — that table has existed since the
Phase 0 schema but nothing has ever written to it until now.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.scoring import score_text, MINIMUM_SCORE_TO_SUGGEST
from app.matching_scoring import combine_score


async def _load_confirmed_capabilities(session: AsyncSession, product_id: str):
    result = await session.execute(
        text("""
            select pc.capability_id, ct.code, ct.label
            from product_capabilities pc
            join capability_taxonomy ct on ct.id = pc.capability_id
            where pc.product_id = :product_id and pc.classified_by = 'analyst'
        """),
        {"product_id": product_id},
    )
    return result.all()


async def _load_classification_codes(session: AsyncSession, capability_id: str) -> set[str]:
    """
    Unions NAICS and CPV mappings for a capability into one set of
    candidate codes. Programmes from any source are matched against
    this combined set — the programmes.naics_code column holds
    either code type depending on which source ingested the row
    (see README "Known naming debt"), so a single combined lookup
    correctly catches both without needing to know in advance which
    scheme a given programme uses.
    """
    naics_result = await session.execute(
        text("select naics_code from taxonomy_naics_mapping where capability_id = :cid"),
        {"cid": capability_id},
    )
    cpv_result = await session.execute(
        text("select cpv_code from taxonomy_cpv_mapping where capability_id = :cid"),
        {"cid": capability_id},
    )
    codes = {row.naics_code for row in naics_result} | {row.cpv_code for row in cpv_result}
    return codes


async def _load_taxonomy_keywords_for_capability(session: AsyncSession, capability_id: str):
    result = await session.execute(
        text("""
            select k.capability_id, c.code, c.label, c.sector, k.keyword, k.weight
            from capability_taxonomy_keywords k
            join capability_taxonomy c on c.id = k.capability_id
            where k.capability_id = :cid
        """),
        {"cid": capability_id},
    )
    return result.all()


async def _load_candidate_programmes(session: AsyncSession, classification_codes: set[str]):
    if not classification_codes:
        return []
    result = await session.execute(
        text("""
            select id, name, country, organization_id, stage, naics_code, source_id,
                   set_aside_code, set_aside_description, response_deadline
            from programmes
            where naics_code = any(:codes)
        """),
        {"codes": list(classification_codes)},
    )
    return result.all()


async def match_product_to_programmes(session: AsyncSession, product_id: str) -> list[dict]:
    capabilities = await _load_confirmed_capabilities(session, product_id)
    matches = []

    for cap in capabilities:
        classification_codes = await _load_classification_codes(session, cap.capability_id)
        if not classification_codes:
            continue  # no mapping curated yet for this capability — nothing to match against

        candidate_programmes = await _load_candidate_programmes(session, classification_codes)
        keyword_rows = await _load_taxonomy_keywords_for_capability(session, cap.capability_id)

        for prog in candidate_programmes:
            category_match = prog.naics_code in classification_codes
            keyword_candidates = score_text(prog.name, keyword_rows)
            keyword_score = keyword_candidates[0]["score"] if keyword_candidates else 0
            matched_keywords = keyword_candidates[0]["matched_keywords"] if keyword_candidates else []

            combined = combine_score(category_match, keyword_score)
            if combined["total_score"] < MINIMUM_SCORE_TO_SUGGEST:
                continue

            matches.append({
                "programme_id": str(prog.id),
                "programme_name": prog.name,
                "capability_id": str(cap.capability_id),
                "capability_code": cap.code,
                "organization_id": str(prog.organization_id) if prog.organization_id else None,
                "naics_match": category_match,  # kept for backward compatibility — see module docstring
                "matched_classification_code": prog.naics_code if category_match else None,
                "set_aside_code": prog.set_aside_code,
                "set_aside_description": prog.set_aside_description,
                "response_deadline": prog.response_deadline,
                "keyword_score": keyword_score,
                "matched_keywords": matched_keywords,
                "total_score": combined["total_score"],
                "confidence": combined["confidence"],
            })

    matches.sort(key=lambda m: m["total_score"], reverse=True)
    return matches
