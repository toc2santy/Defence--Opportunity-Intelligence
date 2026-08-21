"""
Pure combination logic for Phase 3 matching — no I/O, same
separation pattern as app/scoring.py (Phase 1). Combines two
independent signals into one score:

  1. A classification-code-to-taxonomy mapping match (a curated
     table lookup — coarse but fast, grounded in official government
     classification). Originally NAICS-only when this module was
     first built; now also covers CPV codes since UK Find a Tender
     was added — renamed from NAICS_MATCH_BONUS/naics_match to
     CATEGORY_MATCH_BONUS/category_match to reflect that honestly.
     This rename is purely internal (nothing here is serialized to
     an API response under these names), so it required no changes
     to the frontend or the /products/{id}/match-programmes response
     contract, which still uses "naics_match" for backward
     compatibility — see app/programme_matching.py.
  2. A keyword re-score of the programme's own title against that
     specific capability's keyword set (reuses Phase 1's scoring
     engine) — this is what filters out the false positives a
     classification-code-only match lets through, e.g. a "GNSS
     dredging system" programme sharing a NAICS code with genuine
     UAV/sensor work despite having nothing to do with either.
"""

from typing import TypedDict

CATEGORY_MATCH_BONUS = 3
HIGH_CONFIDENCE_THRESHOLD = 6
MEDIUM_CONFIDENCE_THRESHOLD = 3
MAX_SCORE = 100  # matches the opportunities.score check constraint (0-100)


class CombinedScore(TypedDict):
    total_score: int
    confidence: str


def combine_score(category_match: bool, keyword_score: int) -> CombinedScore:
    total = (CATEGORY_MATCH_BONUS if category_match else 0) + keyword_score
    total = min(total, MAX_SCORE)

    # Real evidence from the first live run against actual SAM.gov
    # data caught this: a classification-code-only match with zero
    # keyword corroboration (e.g. "GPS HATCH SYSTEMS" or "SEARCH
    # LIGHT" sharing a broad NAICS code with genuine radar/sensor
    # work) was being labeled "medium confidence" — which overstates
    # it. Sharing a government purchasing category is real but weak
    # signal on its own; it should never outrank "low" unless the
    # programme's own text actually corroborates it. Applies equally
    # to CPV-based matches, same underlying risk.
    if keyword_score == 0:
        confidence = "low"
    elif total >= HIGH_CONFIDENCE_THRESHOLD:
        confidence = "high"
    elif total >= MEDIUM_CONFIDENCE_THRESHOLD:
        confidence = "medium"
    else:
        confidence = "low"

    return {"total_score": total, "confidence": confidence}
