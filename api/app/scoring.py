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
import unicodedata
from typing import TypedDict

# Score thresholds — deliberately conservative. A single generic
# weight-1 keyword match should not produce a "high confidence"
# classification; it takes either one strong (weight-3) term plus
# a supporting one, or multiple corroborating terms.
HIGH_CONFIDENCE_THRESHOLD = 6
MEDIUM_CONFIDENCE_THRESHOLD = 3
MINIMUM_SCORE_TO_SUGGEST = 2  # below this, don't bother suggesting at all


def fold(text: str) -> str:
    """
    Lower-cases and strips accents, so "NAVEGACIÓN" and "navegacion"
    are the same word to the scorer.

    WHY THIS IS NEEDED AND WHY IT IS SAFE: with Colombia (SECOP II)
    ingested, programme titles now arrive in Spanish — and the feed
    itself is inconsistent about accents, writing the same force as
    both "EJERCITO" and "EJÉRCITO". Without folding, a Spanish keyword
    would have to be stored twice to catch both spellings, and the
    wrong half of the pair would silently never match. Folding also
    helps the EU TED rows already in the database, whose titles are in
    24 languages.

    It cannot change the result for an unaccented input: folding is
    the identity function on plain ASCII, so every English keyword and
    every English product description scores exactly as before.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


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
    normalized = fold(input_text)
    scores: dict[str, Candidate] = {}

    for capability_id, code, label, sector, keyword, weight in taxonomy_rows:
        # Word-boundary match so "ai" doesn't match inside "maintain",
        # and multi-word phrases like "electronic warfare" match as
        # a unit rather than any substring appearance. Both sides are
        # folded, so an accented keyword matches an unaccented title
        # and vice versa.
        pattern = r"\b" + re.escape(fold(keyword)) + r"\b"
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
