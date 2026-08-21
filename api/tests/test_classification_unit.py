"""
Unit tests for app.classification.score_text — deliberately kept
separate from the integration tests. This function takes no
database, no network, no async — so these tests run in
milliseconds and should be the first thing you run after touching
the scoring logic itself.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scoring import score_text, HIGH_CONFIDENCE_THRESHOLD, MEDIUM_CONFIDENCE_THRESHOLD


# Small fixture standing in for what would normally come from the
# capability_taxonomy_keywords table — (capability_id, code, label, sector, keyword, weight)
FIXTURE_ROWS = [
    ("cap-uav", "UAV.INTEGRATION", "Tactical UAV Systems Subsystem", "UAV / UAS", "uav", 3),
    ("cap-uav", "UAV.INTEGRATION", "Tactical UAV Systems Subsystem", "UAV / UAS", "drone", 3),
    ("cap-uav", "UAV.INTEGRATION", "Tactical UAV Systems Subsystem", "UAV / UAS", "unmanned aerial", 3),
    ("cap-comms", "COMMS.SECURE.TACTICAL", "Secure Tactical Communication Subsystem", "Secure Communications", "secure communication", 3),
    ("cap-comms", "COMMS.SECURE.TACTICAL", "Secure Tactical Communication Subsystem", "Secure Communications", "encrypted", 3),
    ("cap-comms", "COMMS.SECURE.TACTICAL", "Secure Tactical Communication Subsystem", "Secure Communications", "communication", 1),
    ("cap-radar", "SENSING.RADAR", "Radar & Sensing Subsystem", "Radar", "radar", 3),
]


def test_no_match_returns_empty():
    candidates = score_text("a generic office chair with wheels", FIXTURE_ROWS)
    assert candidates == []


def test_single_strong_keyword_matches():
    candidates = score_text("This is a UAV platform", FIXTURE_ROWS)
    assert len(candidates) == 1
    assert candidates[0]["code"] == "UAV.INTEGRATION"
    assert "uav" in candidates[0]["matched_keywords"]


def test_multiple_keywords_for_same_capability_sum_scores():
    candidates = score_text("This UAV is also a drone with unmanned aerial capability", FIXTURE_ROWS)
    assert len(candidates) == 1
    # three weight-3 keywords should sum to 9
    assert candidates[0]["score"] == 9
    assert set(candidates[0]["matched_keywords"]) == {"uav", "drone", "unmanned aerial"}


def test_multiple_candidates_ranked_by_score():
    text_input = "Secure communication system, encrypted, for UAV integration"
    candidates = score_text(text_input, FIXTURE_ROWS)
    codes = [c["code"] for c in candidates]
    # comms should outrank uav here: "secure communication"(3) + "communication"(1) + "encrypted"(3) = 7
    # vs uav: "uav"(3) = 3
    assert codes[0] == "COMMS.SECURE.TACTICAL"
    assert candidates[0]["score"] == 7


def test_confidence_thresholds():
    high = score_text("secure communication and encrypted comms, this is a communication device", FIXTURE_ROWS)
    # sanity: our fixture text should clear the high threshold for comms
    comms = next(c for c in high if c["code"] == "COMMS.SECURE.TACTICAL")
    assert comms["score"] >= HIGH_CONFIDENCE_THRESHOLD
    assert comms["confidence"] == "high"

    low = score_text("just a communication", FIXTURE_ROWS)
    assert low[0]["confidence"] == "low"
    assert low[0]["score"] < MEDIUM_CONFIDENCE_THRESHOLD


def test_word_boundary_prevents_false_substring_match():
    # "radar" should NOT match inside an unrelated word containing
    # the same letters as a substring (guards against overly loose
    # matching — this was a real risk with the prototype's simple
    # `.includes()` check, which this rules-based engine deliberately
    # avoids by using word-boundary regex).
    candidates = score_text("a word like nonradarword should not match", FIXTURE_ROWS)
    assert candidates == []


def test_case_insensitive_matching():
    candidates = score_text("This Is A UAV Platform", FIXTURE_ROWS)
    assert len(candidates) == 1
    assert candidates[0]["code"] == "UAV.INTEGRATION"
