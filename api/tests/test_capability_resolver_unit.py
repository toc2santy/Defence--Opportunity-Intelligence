"""
Unit tests for app.capability_resolver.resolve_capability — no
network, no database. build_code_index needs a session and is
exercised indirectly by the integration test in
test_customer_intelligence_trigger.py; this file constructs a
CodeIndex by hand to test the pure resolution logic in isolation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.capability_resolver import CodeIndex, resolve_capabilities, resolve_capability


def _index():
    return CodeIndex(
        exact={
            "541715": [("CYBER.DEFENCE", "Cyber Defence Capability")],
            "35410000": [("LAND.SYSTEMS", "Land Systems Platform Subsystem")],
        },
        prefixes=[
            ("411155", "SONAR.PASSIVE", "Passive Sonar Detection Subsystem"),
            ("4111", "SENSORS.GENERAL", "General Sensor Subsystem"),
            ("25", "LAND.SYSTEMS", "Land Systems Platform Subsystem"),
        ],
    )


def test_exact_naics_match():
    assert resolve_capability("541715", _index()) == ("CYBER.DEFENCE", "Cyber Defence Capability")


def test_exact_cpv_match():
    assert resolve_capability("35410000", _index()) == (
        "LAND.SYSTEMS", "Land Systems Platform Subsystem",
    )


def test_unspsc_prefix_match():
    assert resolve_capability("41111500", _index()) == (
        "SENSORS.GENERAL", "General Sensor Subsystem",
    )


def test_longest_unspsc_prefix_wins():
    # '411155' (6 digits) must win over the broader '4111' (4 digits)
    # for a code that matches both — this is the whole reason
    # build_code_index sorts prefixes longest-first.
    assert resolve_capability("41115500", _index()) == (
        "SONAR.PASSIVE", "Passive Sonar Detection Subsystem",
    )


def test_exact_match_checked_before_prefix():
    index = CodeIndex(
        exact={"25172800": [("MISSILES.PRECISION", "Missiles & Precision Systems Subsystem")]},
        prefixes=[("25", "LAND.SYSTEMS", "Land Systems Platform Subsystem")],
    )
    assert resolve_capability("25172800", index) == (
        "MISSILES.PRECISION", "Missiles & Precision Systems Subsystem",
    )


def test_unmapped_code_returns_none():
    assert resolve_capability("99999999", _index()) is None


def test_none_and_empty_code_return_none():
    assert resolve_capability(None, _index()) is None
    assert resolve_capability("", _index()) is None


def test_empty_index_never_crashes():
    assert resolve_capability("41115500", CodeIndex()) is None


# --- co-mapped codes: the regression this module was rewritten for ---
# CPV 35400000 is deliberately mapped to BOTH LAND.SYSTEMS and
# MANUFACTURING.DEFENCE in db/migrations/010. An earlier version kept
# a dict keyed by code, so one silently overwrote the other and every
# 35400000 programme was attributed to whichever was inserted last.

CO_MAPPED = CodeIndex(
    exact={
        "35400000": [
            ("LAND.SYSTEMS", "Land Systems Platform Subsystem"),
            ("MANUFACTURING.DEFENCE", "Defence Manufacturing Capability"),
        ],
    },
    prefixes=[
        ("4111", "SENSORS.GENERAL", "General Sensor Subsystem"),
        ("4111", "SENSING.RADAR", "Radar & Sensing Subsystem"),
        ("411155", "SONAR.PASSIVE", "Passive Sonar Detection Subsystem"),
    ],
)


def test_co_mapped_exact_code_returns_every_capability():
    resolved = resolve_capabilities("35400000", CO_MAPPED)
    assert [c for c, _ in resolved] == ["LAND.SYSTEMS", "MANUFACTURING.DEFENCE"]


def test_co_mapped_prefix_returns_every_capability_at_winning_length():
    # Both SENSORS.GENERAL and SENSING.RADAR map '4111' — both must
    # come back, not just whichever is first.
    resolved = resolve_capabilities("41112200", CO_MAPPED)
    assert sorted(c for c, _ in resolved) == ["SENSING.RADAR", "SENSORS.GENERAL"]


def test_longer_prefix_still_beats_shorter_when_co_mapped():
    # '411155' (6) must win outright over the two '4111' (4) mappings.
    resolved = resolve_capabilities("41115500", CO_MAPPED)
    assert [c for c, _ in resolved] == ["SONAR.PASSIVE"]


def test_singular_wrapper_returns_first_of_several():
    assert resolve_capability("35400000", CO_MAPPED) == (
        "LAND.SYSTEMS", "Land Systems Platform Subsystem",
    )


def test_resolve_capabilities_empty_for_unmapped():
    assert resolve_capabilities("99999999", CO_MAPPED) == []
    assert resolve_capabilities(None, CO_MAPPED) == []
