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
from app.classification import classify_text
from app.cppp_india_normalize import INDIA_DEFENCE_ORG_CODE
from app.south_africa_normalize import SOUTH_AFRICA_DEFENCE_ORG_CODE

# Every source whose defence-relevance signal is the buying
# organisation rather than a real classification code (India, South
# Africa — see each normalizer's module docstring) contributes a
# sentinel code here. All are treated identically by the matching
# loop below: added to every capability's candidate set, but a
# sentinel-only match with zero keyword corroboration is dropped
# rather than kept, because a sentinel alone distinguishes nothing
# between capabilities.
ORGANISATION_SENTINEL_CODES = {INDIA_DEFENCE_ORG_CODE, SOUTH_AFRICA_DEFENCE_ORG_CODE}


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

    # India (CPPP) has no classification scheme to curate a mapping
    # against — defence-relevance there is established by the
    # publishing organisation instead, and those programmes carry the
    # INDIA_DEFENCE_ORG_CODE sentinel (see app/cppp_india_normalize).
    # Adding it to every capability's code set is what makes those
    # programmes candidates at all; which capability each one actually
    # belongs to is then decided purely by the keyword re-score in
    # match_product_to_programmes, which additionally REQUIRES keyword
    # corroboration for sentinel matches.
    codes |= ORGANISATION_SENTINEL_CODES
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


async def _load_unspsc_prefixes(session: AsyncSession, capability_id: str) -> set[str]:
    """
    UNSPSC (CanadaBuys) is matched by PREFIX, not by exact equality,
    because the standard is hierarchical: 8 digits encode
    Segment/Family/Class/Commodity. A tender carries a specific
    commodity code like 25172800 that no curated table could
    enumerate, so the mapping stores the family prefix ('2517') and
    matching walks down from it. See db/migrations/015.
    """
    result = await session.execute(
        text("select unspsc_prefix from taxonomy_unspsc_mapping where capability_id = :cid"),
        {"cid": capability_id},
    )
    return {row.unspsc_prefix for row in result}


async def _load_candidate_programmes(
    session: AsyncSession, classification_codes: set[str], unspsc_prefixes: set[str]
):
    if not classification_codes and not unspsc_prefixes:
        return []
    result = await session.execute(
        text("""
            select p.id, p.name, p.country, p.organization_id, p.stage, p.naics_code, p.source_id,
                   p.set_aside_code, p.set_aside_description, p.response_deadline,
                   s.name as source_name
            from programmes p
            left join sources s on s.id = p.source_id
            where p.naics_code = any(:codes)
               or (:has_prefixes and p.naics_code like any(:prefix_patterns))
        """),
        {
            "codes": list(classification_codes),
            "has_prefixes": bool(unspsc_prefixes),
            "prefix_patterns": [f"{p}%" for p in unspsc_prefixes] or [""],
        },
    )
    return result.all()


async def match_product_to_programmes(session: AsyncSession, product_id: str) -> dict:
    """
    Returns {"matches": [...], "trace": {...}} — the trace is not
    decorative. Every number in it is something this function itself
    actually counted while running (capabilities checked, distinct
    programmes examined, which sources those programmes came from,
    how many passed the score threshold) — nothing computed
    separately just for display. This is what the frontend's Match
    Trace panel shows a customer, on the same "no invented numbers"
    footing as everything else in this project.
    """
    capabilities = await _load_confirmed_capabilities(session, product_id)
    matches = []
    examined_programme_ids: set[str] = set()
    sources_touched: set[str] = set()
    capability_trace = []

    for cap in capabilities:
        classification_codes = await _load_classification_codes(session, cap.capability_id)
        if not classification_codes:
            # Defensive only. Since the organisation-sentinel codes are
            # always added, this set is never actually empty now —
            # which is itself a deliberate consequence: a capability
            # with no curated NAICS/CPV mapping can still match Indian
            # or South African programmes on keyword evidence alone,
            # where before it matched nothing.
            continue

        unspsc_prefixes = await _load_unspsc_prefixes(session, cap.capability_id)
        candidate_programmes = await _load_candidate_programmes(
            session, classification_codes, unspsc_prefixes
        )
        keyword_rows = await _load_taxonomy_keywords_for_capability(session, cap.capability_id)
        capability_trace.append({
            "code": cap.code, "label": cap.label,
            "candidates_examined": len(candidate_programmes),
        })

        for prog in candidate_programmes:
            examined_programme_ids.add(str(prog.id))
            if prog.source_name:
                sources_touched.add(prog.source_name)
            code = prog.naics_code or ""
            category_match = (
                code in classification_codes
                or any(code.startswith(p) for p in unspsc_prefixes)
            )
            keyword_candidates = score_text(prog.name, keyword_rows)
            keyword_score = keyword_candidates[0]["score"] if keyword_candidates else 0
            matched_keywords = keyword_candidates[0]["matched_keywords"] if keyword_candidates else []

            # A real NAICS/CPV code identifies a specific category, so
            # a code-only match is weak-but-real evidence and is kept
            # (capped at "low" by combine_score). An organisation
            # sentinel is different in kind: it says only "a defence
            # body from that country published this", which is true of
            # every programme from that source and so distinguishes
            # nothing between capabilities. Without keyword
            # corroboration it would make every one of that source's
            # tenders match every capability, so it is dropped outright
            # rather than surfaced as a weak match.
            #
            # A real false positive, user-reported (2026-09): a CPPP
            # (IN-DEF sentinel) tender about street-light repair scored
            # 92%/high for a UAV product, because its title contains
            # "...UAV KUMBHIGRAM..." — a real Indian military station
            # name (Assam), not a reference to unmanned aerial
            # vehicles, but the bare 3-letter keyword "uav" still
            # matches it at a genuine word boundary. A sentinel row has
            # ZERO real classification evidence to begin with, so its
            # keyword corroboration is the ONLY thing standing between
            # "genuinely relevant" and "coincidental short-acronym
            # collision" — a short, space-less token (uav, uas, vtol,
            # dron) is real evidence for a CODE-matched programme
            # (where it only adds confidence on top of already-real
            # category evidence) but too weak to be the SOLE evidence
            # for a sentinel row. Multi-word phrases ("unmanned
            # aerial") and longer single words (5+ characters, "drone",
            # "quadcopter") are far less likely to collide with an
            # unrelated place/unit name by coincidence, so those still
            # count. This does not touch keyword scoring for any
            # code-matched programme — only what's ACCEPTABLE AS THE
            # ONLY EVIDENCE for a sentinel-only row.
            sentinel_row = prog.naics_code in ORGANISATION_SENTINEL_CODES
            if sentinel_row:
                corroborating_keywords = [kw for kw in matched_keywords if len(kw) >= 5 or " " in kw]
                if not corroborating_keywords:
                    continue

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
    return {
        "matches": matches,
        "trace": {
            "capabilities_checked": capability_trace,
            "programmes_examined": len(examined_programme_ids),
            "sources_touched": sorted(sources_touched),
            "opportunities_matched": len(matches),
        },
    }


async def preview_match_for_text(session: AsyncSession, query_text: str, max_capabilities: int = 3) -> dict:
    """
    The public, un-authenticated "does this actually work" homepage
    widget — free text in, a real count out, no signup needed. Reuses
    exactly the classification scoring an actual product goes through
    (app.classification.classify_text) to find which capabilities the
    text resembles, then counts real ingested programmes against those
    capabilities' classification codes.

    Deliberately coarser than match_product_to_programmes in one way:
    ORGANISATION_SENTINEL_CODES are excluded from the count here. The
    real matching pipeline only lets a sentinel match through with
    keyword corroboration against one specific programme's title —
    there is no per-programme text to re-score against in a bare
    aggregate count, so leaving the sentinels in would silently credit
    every capability with the same India/South-Africa programme count
    regardless of relevance, which is exactly the kind of invented
    number this platform's evidence discipline exists to prevent.

    Returns only counts and capability labels — never a programme
    name, link or contact — on purpose: this is the "it works" proof,
    not a way to browse the real dataset without an account.
    """
    candidates = await classify_text(session, query_text)
    top = [c for c in candidates if c["score"] >= MINIMUM_SCORE_TO_SUGGEST][:max_capabilities]
    if not top:
        return {"matched_capabilities": [], "programme_count": 0, "country_count": 0}

    all_codes: set[str] = set()
    all_prefixes: set[str] = set()
    for cand in top:
        codes = await _load_classification_codes(session, cand["capability_id"])
        all_codes |= codes - ORGANISATION_SENTINEL_CODES
        all_prefixes |= await _load_unspsc_prefixes(session, cand["capability_id"])

    if not all_codes and not all_prefixes:
        return {
            "matched_capabilities": [{"label": c["label"], "sector": c["sector"]} for c in top],
            "programme_count": 0,
            "country_count": 0,
        }

    result = await session.execute(
        text("""
            select count(distinct id) as programme_count, count(distinct country) as country_count
            from programmes
            where naics_code = any(:codes)
               or (:has_prefixes and naics_code like any(:prefix_patterns))
        """),
        {
            "codes": list(all_codes),
            "has_prefixes": bool(all_prefixes),
            "prefix_patterns": [f"{p}%" for p in all_prefixes] or [""],
        },
    )
    row = result.first()
    return {
        "matched_capabilities": [{"label": c["label"], "sector": c["sector"]} for c in top],
        "programme_count": row.programme_count,
        "country_count": row.country_count,
    }
