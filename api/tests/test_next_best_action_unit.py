"""
Unit tests for app.next_best_action — no network, no database.
`today` is always passed explicitly so these tests never depend on
the real current date.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.next_best_action import suggest_next_action

TODAY = date(2026, 8, 22)


def test_won_suggests_onboarding():
    assert "onboarding" in suggest_next_action("won", "high", None, TODAY).lower()


def test_lost_suggests_no_further_action():
    assert "no further action" in suggest_next_action("lost", "low", None, TODAY).lower()


def test_urgent_deadline_overrides_stage_and_confidence():
    action = suggest_next_action("lead", "low", "2026-08-25", TODAY)
    assert "urgent" in action.lower()
    assert "3 day" in action


def test_deadline_far_out_does_not_trigger_urgency():
    action = suggest_next_action("lead", "high", "2027-01-01", TODAY)
    assert "urgent" not in action.lower()


def test_deadline_exactly_at_urgent_threshold():
    action = suggest_next_action("lead", "high", "2026-08-29", TODAY)  # 7 days out
    assert "urgent" in action.lower()


def test_deadline_one_day_past_threshold_not_urgent():
    action = suggest_next_action("lead", "high", "2026-08-30", TODAY)  # 8 days out
    assert "urgent" not in action.lower()


def test_passed_deadline_flags_for_review():
    action = suggest_next_action("lead", "high", "2026-08-01", TODAY)
    assert "passed" in action.lower()


def test_early_stage_high_confidence_suggests_outreach():
    action = suggest_next_action("lead", "high", None, TODAY)
    assert "reach out" in action.lower()


def test_early_stage_low_confidence_suggests_recheck():
    action = suggest_next_action("qualified", "low", None, TODAY)
    assert "confidence" in action.lower() or "recheck" in action.lower() or "re-check" in action.lower()


def test_mid_stage_suggests_technical_qualification():
    assert "qualification" in suggest_next_action("rfp", "medium", None, TODAY).lower()


def test_late_stage_suggests_finalising():
    assert "finalise" in suggest_next_action("negotiation", "high", None, TODAY).lower()


def test_late_stage_with_deadline_includes_days_remaining():
    action = suggest_next_action("proposal", "high", "2026-09-21", TODAY)  # 30 days out
    assert "30 days" in action


def test_unrecognised_stage_falls_back_safely():
    action = suggest_next_action("some_future_stage_not_yet_added", None, None, TODAY)
    assert isinstance(action, str) and len(action) > 0


def test_malformed_deadline_does_not_crash():
    action = suggest_next_action("lead", "high", "not-a-real-date", TODAY)
    assert isinstance(action, str) and len(action) > 0


def test_full_datetime_deadline_string_parses():
    action = suggest_next_action("lead", "high", "2026-08-25T18:00:00", TODAY)
    assert "urgent" in action.lower()


# --- set-aside eligibility + owner awareness, added after Report Intel review ---

def test_set_aside_note_appended_in_early_stage():
    msg = suggest_next_action(
        "lead", "high", None,
        set_aside_code="SBA", set_aside_description="Total Small Business Set-Aside",
    )
    assert "Total Small Business Set-Aside" in msg
    assert "reach out to the buying organisation" in msg  # base rule text unchanged


def test_no_set_aside_note_when_none_published():
    msg = suggest_next_action("lead", "high", None)
    assert "eligibility restriction" not in msg


def test_set_aside_note_not_appended_once_past_early_stage():
    """Eligibility is a pre-investment check — not useful noise once past the early stage."""
    msg = suggest_next_action(
        "proposal", "high", None, set_aside_code="SBA", set_aside_description="Small Business Set-Aside",
    )
    assert "eligibility restriction" not in msg


def test_missing_owner_prefixes_every_open_stage_message():
    msg = suggest_next_action("lead", "high", None, has_owner=False)
    assert msg.startswith("No owner assigned yet")
    assert "reach out to the buying organisation" in msg


def test_owner_present_gives_no_prefix():
    msg = suggest_next_action("lead", "high", None, has_owner=True)
    assert not msg.startswith("No owner assigned")


def test_missing_owner_note_skipped_on_closed_stages():
    """Won/lost are terminal — telling someone to 'assign an owner' on a closed deal is noise."""
    msg = suggest_next_action("won", None, None, has_owner=False)
    assert not msg.startswith("No owner assigned")


def test_both_notes_can_stack():
    msg = suggest_next_action(
        "qualified", None, None, set_aside_code="8A",
        set_aside_description="8(a) Set-Aside", has_owner=False,
    )
    assert msg.startswith("No owner assigned yet")
    assert "8(a) Set-Aside" in msg


# --- due_date overdue awareness, added for team visibility ---------

def test_overdue_due_date_is_flagged():
    msg = suggest_next_action("qualified", "high", None, today=date(2026, 9, 20), due_date="2026-09-15")
    assert msg.startswith("⚠ Overdue by 5 days")


def test_due_date_today_is_not_flagged_overdue():
    """Due today is not overdue yet — overdue means the date has genuinely passed."""
    msg = suggest_next_action("qualified", "high", None, today=date(2026, 9, 15), due_date="2026-09-15")
    assert not msg.startswith("⚠ Overdue")


def test_future_due_date_is_not_flagged():
    msg = suggest_next_action("qualified", "high", None, today=date(2026, 9, 10), due_date="2026-09-15")
    assert not msg.startswith("⚠ Overdue")


def test_overdue_note_skipped_on_closed_stages():
    msg = suggest_next_action("won", None, None, today=date(2026, 9, 20), due_date="2026-09-01")
    assert not msg.startswith("⚠ Overdue")


def test_overdue_and_no_owner_can_stack():
    msg = suggest_next_action(
        "lead", "high", None, today=date(2026, 9, 20), due_date="2026-09-15", has_owner=False,
    )
    assert msg.startswith("No owner assigned yet")
    assert "⚠ Overdue by 5 days" in msg
