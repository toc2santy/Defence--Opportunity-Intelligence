"""Pure decision rules of the automatic data-health checks (no DB, no network)."""

from datetime import timedelta

from app.data_health import freshness_severity, row_drop_severity, severity_for_percent


def test_percent_thresholds():
    assert severity_for_percent(29.9, 30) == "ok"
    assert severity_for_percent(30, 30) == "warn"


def test_freshness_bands():
    assert freshness_severity(timedelta(hours=20)) == "ok"
    assert freshness_severity(timedelta(days=4)) == "warn"
    assert freshness_severity(timedelta(days=11)) == "error"
    assert freshness_severity(None) == "warn", "never ingested is a warning, not silently fine"


def test_a_big_drop_in_stored_rows_is_an_error_but_normal_change_is_not():
    assert row_drop_severity(1000, 790) == "error"      # -21%
    assert row_drop_severity(1000, 850) == "ok"         # -15%
    assert row_drop_severity(1000, 1400) == "ok"        # growth
    assert row_drop_severity(None, 5) == "ok"           # no previous run to compare with
