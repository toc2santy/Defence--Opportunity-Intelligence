"""Unit test for the fiscal-year label used to build the award-notice URL."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.canada_buys_ingestion import current_fiscal_year_label


def test_april_starts_a_new_fiscal_year():
    assert current_fiscal_year_label(date(2026, 4, 1)) == "2026-2027"


def test_march_is_still_the_previous_fiscal_year():
    assert current_fiscal_year_label(date(2026, 3, 31)) == "2025-2026"


def test_mid_fiscal_year():
    assert current_fiscal_year_label(date(2026, 9, 16)) == "2026-2027"


def test_january_is_still_the_fiscal_year_that_started_the_prior_april():
    assert current_fiscal_year_label(date(2027, 1, 15)) == "2026-2027"
