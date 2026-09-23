"""
Fit & Feasibility Score — a transparent, rules-based readiness signal
for one opportunity, not a statistically-calibrated probability of
winning. No mocking, no I/O: every input here is data already fetched
elsewhere, same separation as scoring.py/matching_scoring.py.

WHY NOT "PROBABILITY": a real probability needs real historical
Won/Lost outcomes to calibrate against, which this platform does not
yet have at scale. A number presented as a probability without that
grounding would be an invented statistic wearing a measured one's
clothes — exactly the class of overclaim this project's evidence
model exists to prevent (see matching_scoring.py's own docstring on
why a category-only match is capped at "low", not blended in as if it
were real corroboration). Once tenants accumulate real Won/Lost
history on this platform (opportunities.stage already has both), a
v2 could calibrate an actual empirical win-rate model — a stated
future step, not a promise made by this module's naming.

FOUR SCORED PARAMETERS (P1/P3/P4/P5) sum to a percentage. A FIFTH
(P2, stated restriction) is deliberately NOT part of the sum — it is
a GATE: a real restriction the source itself states is not "somewhat
less attractive", it's "you may not be allowed to bid at all", a
categorically different signal from the other four, so it caps the
score hard rather than being quietly blended in as a point deduction.
This module only ever surfaces what the source ITSELF stated — it
never determines legal eligibility, matching this platform's existing
"not legal advice, confirm with export-control/trade-compliance
counsel" stance (see CLAUDE.md's Known Limitations).

MISSING DATA IS EXCLUDED, NEVER DEFAULTED: a parameter with nothing
to go on is left out of both the numerator and denominator, not
silently scored as 0 — an absent signal must never masquerade as a
negative one. The percentage is always computed only over the
signals that were actually available, and the response says exactly
how many that was, so "62%" never quietly means "62% out of signals
we mostly didn't have."
"""

from datetime import date
from typing import Optional, TypedDict

DISCLAIMER = (
    "This Fit & Feasibility Score is a data-driven readiness signal built from "
    "real signals on this specific tender — it is not a statistically "
    "calibrated probability of winning, and no platform can predict a "
    "procurement outcome with certainty. Qualifying for a tender and being "
    "awarded it are different things; this score reflects the former as "
    "closely as available data allows."
)

CONFIDENCE_POINTS = {"high": 40, "medium": 25, "low": 10}
MAX_CONFIDENCE_POINTS = 40
MAX_DEADLINE_POINTS = 20
MAX_VALUE_POINTS = 20
MAX_BUYER_POINTS = 20

# Restriction-code sentinels actually seen live meaning "no
# restriction" (SAM.gov prints "NONE" with description "No Set aside
# used") — anything else present is treated as a real, stated
# restriction worth flagging.
_NO_RESTRICTION_CODES = {"NONE", "N/A", ""}


class ScoreBreakdownItem(TypedDict):
    parameter: str
    points: Optional[int]
    max_points: int
    note: str


class FitScoreResult(TypedDict):
    score_percent: Optional[int]
    signals_available: int
    signals_total: int
    breakdown: list[ScoreBreakdownItem]
    restriction_stated: bool
    restriction_text: Optional[str]
    disclaimer: str


def _confidence_points(confidence: Optional[str]) -> ScoreBreakdownItem:
    if confidence not in CONFIDENCE_POINTS:
        return {
            "parameter": "Match confidence", "points": None, "max_points": MAX_CONFIDENCE_POINTS,
            "note": "No match confidence recorded for this opportunity.",
        }
    pts = CONFIDENCE_POINTS[confidence]
    return {
        "parameter": "Match confidence", "points": pts, "max_points": MAX_CONFIDENCE_POINTS,
        "note": f"{confidence.capitalize()} confidence match against your product's capability.",
    }


def _deadline_points(response_deadline: Optional[str], today: Optional[date] = None) -> ScoreBreakdownItem:
    if not response_deadline:
        return {
            "parameter": "Deadline feasibility", "points": None, "max_points": MAX_DEADLINE_POINTS,
            "note": "No closing date published by the source.",
        }
    try:
        deadline_date = date.fromisoformat(str(response_deadline)[:10])
    except ValueError:
        return {
            "parameter": "Deadline feasibility", "points": None, "max_points": MAX_DEADLINE_POINTS,
            "note": f'The source published "{response_deadline}", which could not be read as a date.',
        }
    today = today or date.today()
    days_left = (deadline_date - today).days
    if days_left < 0:
        return {"parameter": "Deadline feasibility", "points": 0, "max_points": MAX_DEADLINE_POINTS,
                "note": "The response deadline has already passed."}
    if days_left < 3:
        return {"parameter": "Deadline feasibility", "points": 0, "max_points": MAX_DEADLINE_POINTS,
                "note": f"Extremely tight — {days_left} day(s) left, unlikely feasible to prepare a competitive bid."}
    if days_left <= 7:
        return {"parameter": "Deadline feasibility", "points": 5, "max_points": MAX_DEADLINE_POINTS,
                "note": f"Tight timeline — {days_left} days left."}
    if days_left <= 21:
        return {"parameter": "Deadline feasibility", "points": 15, "max_points": MAX_DEADLINE_POINTS,
                "note": f"Reasonable timeline — {days_left} days left."}
    return {"parameter": "Deadline feasibility", "points": 20, "max_points": MAX_DEADLINE_POINTS,
            "note": f"Ample time to prepare a competitive bid — {days_left} days left."}


def _value_points(buyer_avg_award_value: Optional[float], buyer_award_currency: Optional[str]) -> ScoreBreakdownItem:
    if buyer_avg_award_value is None:
        return {
            "parameter": "Value-tier fit", "points": None, "max_points": MAX_VALUE_POINTS,
            "note": "This tender's own value isn't published, and this buyer has no captured award-value history yet.",
        }
    # A deliberately simple presence signal for v1: having ANY real
    # grounded value data for this buyer earns the full points — the
    # goal is "do we have real evidence of this buyer's typical deal
    # size", not a judgement on whether that size is good or bad for
    # THIS tenant, which only the tenant's own business knowledge can
    # decide (see this module's own docstring).
    currency_note = f" {buyer_award_currency}" if buyer_award_currency else ""
    return {
        "parameter": "Value-tier fit", "points": MAX_VALUE_POINTS, "max_points": MAX_VALUE_POINTS,
        "note": f"Estimated from this buyer's own award history (avg.{currency_note} {buyer_avg_award_value:,.0f}) — not the tender's own stated value.",
    }


def _buyer_points(distinct_winner_count: Optional[int]) -> ScoreBreakdownItem:
    if not distinct_winner_count:
        return {
            "parameter": "Buyer competitiveness", "points": None, "max_points": MAX_BUYER_POINTS,
            "note": "No award history available for this buyer yet.",
        }
    if distinct_winner_count >= 5:
        return {
            "parameter": "Buyer competitiveness", "points": 20, "max_points": MAX_BUYER_POINTS,
            "note": f"Buyer has awarded to {distinct_winner_count} different companies in available history — an open competitive field.",
        }
    if distinct_winner_count >= 2:
        return {
            "parameter": "Buyer competitiveness", "points": 10, "max_points": MAX_BUYER_POINTS,
            "note": f"Buyer has awarded to {distinct_winner_count} different companies in available history — a moderately concentrated supplier base.",
        }
    return {
        "parameter": "Buyer competitiveness", "points": 0, "max_points": MAX_BUYER_POINTS,
        "note": "Buyer has awarded to a single company in available history — may indicate an incumbent relationship.",
    }


def _restriction_gate(set_aside_code: Optional[str], set_aside_description: Optional[str]) -> tuple[bool, Optional[str]]:
    code = (set_aside_code or "").strip().upper()
    if not code or code in _NO_RESTRICTION_CODES:
        return False, None
    return True, set_aside_description or set_aside_code


def compute_fit_score(
    confidence: Optional[str],
    set_aside_code: Optional[str],
    set_aside_description: Optional[str],
    response_deadline: Optional[str],
    buyer_avg_award_value: Optional[float],
    buyer_award_currency: Optional[str],
    buyer_distinct_winner_count: Optional[int],
    today: Optional[date] = None,
) -> FitScoreResult:
    items = [
        _confidence_points(confidence),
        _value_points(buyer_avg_award_value, buyer_award_currency),
        _deadline_points(response_deadline, today),
        _buyer_points(buyer_distinct_winner_count),
    ]
    restriction_stated, restriction_text = _restriction_gate(set_aside_code, set_aside_description)

    scored = [i for i in items if i["points"] is not None]
    total_points = sum(i["points"] for i in scored)
    max_points = sum(i["max_points"] for i in scored)

    score_percent = round(100 * total_points / max_points) if max_points > 0 else None
    if restriction_stated and score_percent is not None:
        # A hard cap, not a soft penalty — see module docstring.
        score_percent = min(score_percent, 20)

    return {
        "score_percent": score_percent,
        "signals_available": len(scored),
        "signals_total": len(items),
        "breakdown": items,
        "restriction_stated": restriction_stated,
        "restriction_text": restriction_text,
        "disclaimer": DISCLAIMER,
    }
