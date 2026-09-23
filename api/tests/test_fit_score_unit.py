"""
Pure unit tests for app/fit_score.py — no DB, no network.
"""

from datetime import date

from app.fit_score import compute_fit_score


TODAY = date(2026, 9, 18)


def _score(**kwargs):
    defaults = dict(
        confidence="high",
        set_aside_code=None,
        set_aside_description=None,
        response_deadline="2026-10-20",  # 32 days out
        buyer_avg_award_value=None,
        buyer_award_currency=None,
        buyer_distinct_winner_count=None,
        today=TODAY,
    )
    defaults.update(kwargs)
    return compute_fit_score(**defaults)


def test_all_four_signals_present_gives_full_percentage():
    result = _score(
        confidence="high",
        response_deadline="2026-10-20",  # 32 days -> full 20
        buyer_avg_award_value=500000.0,
        buyer_award_currency="USD",
        buyer_distinct_winner_count=7,
    )
    assert result["score_percent"] == 100
    assert result["signals_available"] == 4
    assert result["signals_total"] == 4
    assert result["restriction_stated"] is False


def test_missing_signals_are_excluded_not_defaulted_to_zero():
    """
    Only match confidence available — the other three must be
    excluded from both numerator and denominator, not scored as 0.
    """
    result = _score(
        confidence="high",
        response_deadline=None,
        buyer_avg_award_value=None,
        buyer_distinct_winner_count=None,
    )
    assert result["signals_available"] == 1
    assert result["signals_total"] == 4
    # High confidence alone = 40/40 available points = 100%
    assert result["score_percent"] == 100


def test_no_signals_available_at_all_is_none_not_zero():
    result = _score(confidence=None, response_deadline=None, buyer_avg_award_value=None, buyer_distinct_winner_count=None)
    assert result["score_percent"] is None
    assert result["signals_available"] == 0


def test_confidence_levels_produce_different_points():
    high = _score(confidence="high", response_deadline=None)["score_percent"]
    medium = _score(confidence="medium", response_deadline=None)["score_percent"]
    low = _score(confidence="low", response_deadline=None)["score_percent"]
    assert high > medium > low


def test_stated_restriction_caps_score_regardless_of_other_signals():
    result = _score(
        confidence="high",
        set_aside_code="8A",
        set_aside_description="8(a) Set-Aside",
        response_deadline="2026-10-20",
        buyer_avg_award_value=500000.0,
        buyer_distinct_winner_count=7,
    )
    assert result["restriction_stated"] is True
    assert result["restriction_text"] == "8(a) Set-Aside"
    assert result["score_percent"] <= 20


def test_none_restriction_code_is_not_treated_as_a_restriction():
    """SAM.gov's own literal sentinel for "no restriction"."""
    result = _score(set_aside_code="NONE", set_aside_description="No Set aside used")
    assert result["restriction_stated"] is False
    assert result["restriction_text"] is None


def test_restriction_falls_back_to_code_when_no_description():
    result = _score(set_aside_code="SDVOSB", set_aside_description=None)
    assert result["restriction_stated"] is True
    assert result["restriction_text"] == "SDVOSB"


def test_deadline_passed_scores_zero_but_is_not_excluded():
    result = _score(response_deadline="2026-09-01")  # before TODAY
    deadline_item = next(i for i in result["breakdown"] if i["parameter"] == "Deadline feasibility")
    assert deadline_item["points"] == 0
    assert "passed" in deadline_item["note"].lower()


def test_deadline_bands():
    def days_out(n):
        from datetime import timedelta
        return (TODAY + timedelta(days=n)).isoformat()

    assert _score(response_deadline=days_out(1))["breakdown"][2]["points"] == 0    # <3 days
    assert _score(response_deadline=days_out(5))["breakdown"][2]["points"] == 5    # 3-7
    assert _score(response_deadline=days_out(15))["breakdown"][2]["points"] == 15  # 8-21
    assert _score(response_deadline=days_out(30))["breakdown"][2]["points"] == 20  # >21


def test_unparseable_deadline_is_excluded_not_zero():
    result = _score(response_deadline="TBD")
    deadline_item = next(i for i in result["breakdown"] if i["parameter"] == "Deadline feasibility")
    assert deadline_item["points"] is None


def test_buyer_competitiveness_bands():
    assert _score(buyer_distinct_winner_count=1, response_deadline=None)["breakdown"][3]["points"] == 0
    assert _score(buyer_distinct_winner_count=3, response_deadline=None)["breakdown"][3]["points"] == 10
    assert _score(buyer_distinct_winner_count=8, response_deadline=None)["breakdown"][3]["points"] == 20
    assert _score(buyer_distinct_winner_count=0, response_deadline=None)["breakdown"][3]["points"] is None
    assert _score(buyer_distinct_winner_count=None, response_deadline=None)["breakdown"][3]["points"] is None


def test_value_points_is_all_or_nothing_presence_signal():
    with_value = _score(buyer_avg_award_value=250000.0, response_deadline=None)["breakdown"][1]
    without_value = _score(buyer_avg_award_value=None, response_deadline=None)["breakdown"][1]
    assert with_value["points"] == 20
    assert without_value["points"] is None


def test_disclaimer_always_present_and_says_not_a_probability():
    result = _score()
    assert "not a statistically" in result["disclaimer"]
    assert "probability" in result["disclaimer"].lower()
