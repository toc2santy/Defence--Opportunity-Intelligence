"""
Unit tests for app.credential_status — no DB. Every assertion here
was already verified standalone before being committed, same
discipline as the rest of the suite.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.credential_status import compute_status, WARNING_THRESHOLD_DAYS


def test_real_key_scenario_89_days_is_ok():
    # The actual SAM.gov key obtained during this project, as of
    # the day it was issued.
    r = compute_status(date(2026, 11, 15), date(2026, 8, 18))
    assert r["days_remaining"] == 89
    assert r["status"] == "ok"


def test_within_warning_window():
    r = compute_status(date(2026, 8, 30), date(2026, 8, 18))
    assert r["days_remaining"] == 12
    assert r["status"] == "warning"


def test_exactly_at_warning_boundary():
    r = compute_status(date(2026, 9, 1), date(2026, 8, 18))
    assert r["days_remaining"] == WARNING_THRESHOLD_DAYS
    assert r["status"] == "warning"  # boundary counts as warning, not ok


def test_one_day_past_warning_boundary_is_ok():
    r = compute_status(date(2026, 9, 2), date(2026, 8, 18))
    assert r["days_remaining"] == WARNING_THRESHOLD_DAYS + 1
    assert r["status"] == "ok"


def test_expired_key():
    r = compute_status(date(2026, 8, 1), date(2026, 8, 18))
    assert r["days_remaining"] == -17
    assert r["status"] == "expired"
    assert "EXPIRED" in r["message"]


def test_expires_today_counts_as_warning_not_expired():
    r = compute_status(date(2026, 8, 18), date(2026, 8, 18))
    assert r["days_remaining"] == 0
    assert r["status"] == "warning"
