"""
Pure rules-based classification scoring — no I/O, no database, no
SQLAlchemy import. This module exists specifically so the unit
tests can import it without needing the full API dependency set
installed, and so the scoring logic itself can be reasoned about
and tested in complete isolation from the database layer.

app/classification.py wraps this with the DB-loading code the API
actually calls.
"""

import re
from typing import TypedDict

# Score thresholds — deliberately conservative. A single generic
# weight-1 keyword match should not produce a "high confidence"
# classification; it takes either one strong (weight-3) term plus
# a supporting one, or multiple corroborating terms.
HIGH_CONFIDENCE_THRESHOLD = 6
MEDIUM_CONFIDENCE_THRESHOLD = 3
MINIMUM_SCORE_TO_SUGGEST = 2  # below this, don't bother suggesting at all


class Candidate(TypedDict):
    capability_id: str
    code: str
    label: str
    sector: str
    score: int
    matched_keywords: list[str]
    confidence: str


def score_text(input_text: str, taxonomy_rows) -> list[Candidate]:
    """
    `taxonomy_rows` is a list of
    (capability_id, code, label, sector, keyword, weight) tuples —
    normally loaded from capability_taxonomy_keywords, but passed
    in directly here so this function has zero I/O of its own.
    """
    normalized = input_text.lower()
    scores: dict[str, Candidate] = {}

    for capability_id, code, label, sector, keyword, weight in taxonomy_rows:
        # Word-boundary match so "ai" doesn't match inside "maintain",
        # and multi-word phrases like "electronic warfare" match as
        # a unit rather than any substring appearance.
        pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
        if re.search(pattern, normalized):
            key = str(capability_id)
            if key not in scores:
                scores[key] = {
                    "capability_id": key,
                    "code": code,
                    "label": label,
                    "sector": sector,
                    "score": 0,
                    "matched_keywords": [],
                    "confidence": "low",
                }
            scores[key]["score"] += weight
            scores[key]["matched_keywords"].append(keyword)

    candidates = sorted(scores.values(), key=lambda c: c["score"], reverse=True)
    for c in candidates:
        if c["score"] >= HIGH_CONFIDENCE_THRESHOLD:
            c["confidence"] = "high"
        elif c["score"] >= MEDIUM_CONFIDENCE_THRESHOLD:
            c["confidence"] = "medium"
        else:
            c["confidence"] = "low"
    return candidates
