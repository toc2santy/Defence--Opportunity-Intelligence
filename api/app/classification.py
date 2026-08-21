"""
Phase 1 — real Capability Intelligence classification.

This replaces the prototype's throwaway logic:

    def classify(t):
        for k in CAPABILITY_LIBRARY:      # 11 hardcoded strings
            if k in t: return CAPABILITY_LIBRARY[k]   # first match wins

with weighted, scored, multi-candidate matching against a real,
extensible taxonomy table — and every result carries the evidence
(which keywords matched, at what weight) instead of just a label.

The actual scoring logic lives in app/scoring.py, which has zero
dependencies beyond the standard library — this module just loads
the taxonomy/keyword data from the database and hands it to that
pure function.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.scoring import (
    Candidate,
    score_text,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    MINIMUM_SCORE_TO_SUGGEST,
)


async def _load_taxonomy_keywords(session: AsyncSession):
    result = await session.execute(
        text("""
            select k.capability_id, c.code, c.label, c.sector, k.keyword, k.weight
            from capability_taxonomy_keywords k
            join capability_taxonomy c on c.id = k.capability_id
        """)
    )
    return result.all()


async def classify_text(session: AsyncSession, input_text: str) -> list[Candidate]:
    """DB-backed entry point the API calls."""
    rows = await _load_taxonomy_keywords(session)
    return score_text(input_text, rows)

