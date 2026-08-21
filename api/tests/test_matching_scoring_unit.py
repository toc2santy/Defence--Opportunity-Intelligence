"""
Unit tests for app.matching_scoring.combine_score — no DB, no
network. Same standalone-verification discipline used for
app.scoring and app.sam_gov_normalize: every assertion here was
run directly before being committed to this file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching_scoring import combine_score, CATEGORY_MATCH_BONUS, MAX_SCORE


def test_naics_only_match():
    # Real evidence from the first live run against actual SAM.gov
    # data (programmes like "GPS HATCH SYSTEMS" and "SEARCH LIGHT"
    # sharing a NAICS code with genuine radar work, but nothing
    # else) showed a NAICS-only match should not read as "medium"
    # confidence — that overstates what's actually known.
    r = combine_score(category_match=True, keyword_score=0)
    assert r["total_score"] == CATEGORY_MATCH_BONUS
    assert r["confidence"] == "low"


def test_keyword_only_no_naics():
    r = combine_score(category_match=False, keyword_score=5)
    assert r["total_score"] == 5
    assert r["confidence"] == "medium"


def test_both_signals_combine_additively():
    r = combine_score(category_match=True, keyword_score=5)
    assert r["total_score"] == 8
    assert r["confidence"] == "high"


def test_weak_signal_is_low_confidence():
    r = combine_score(category_match=False, keyword_score=1)
    assert r["total_score"] == 1
    assert r["confidence"] == "low"


def test_no_signal_at_all():
    r = combine_score(category_match=False, keyword_score=0)
    assert r["total_score"] == 0
    assert r["confidence"] == "low"


def test_score_never_exceeds_max():
    # this matters because opportunities.score has a real database
    # check constraint (0-100) — an uncapped score here would crash
    # the insert, not just look wrong
    r = combine_score(category_match=True, keyword_score=999)
    assert r["total_score"] == MAX_SCORE


def test_naics_match_alone_never_exceeds_low_confidence_regardless_of_bonus_size():
    # Regression test for the exact false positive found in the
    # first live run: "BAND SATCOM & GPS HATCH SYSTEMS" and
    # "CGC KUKUI PERKO SEARCH LIGHT" both matched SENSING.RADAR
    # purely by sharing NAICS 334511, with zero keyword evidence —
    # and were originally mislabeled "medium confidence". A NAICS
    # match with no textual corroboration must always cap at "low",
    # no matter how the bonus/threshold constants change later.
    r = combine_score(category_match=True, keyword_score=0)
    assert r["confidence"] == "low"
