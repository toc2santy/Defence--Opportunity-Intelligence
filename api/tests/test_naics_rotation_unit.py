"""
Unit tests for app.sam_gov_normalize.get_naics_group — no DB, no
network. Every assertion here was run standalone before being
committed to this file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sam_gov_normalize import get_naics_group, DEFENSE_RELEVANT_NAICS, NAICS_GROUP_SIZE


CODES = list(DEFENSE_RELEVANT_NAICS.keys())  # a real, live-verified set — see module docstring for how it's grounded


def test_first_group_is_first_n_codes():
    assert get_naics_group(CODES, 0) == CODES[:3]


def test_second_group_is_next_n_codes():
    assert get_naics_group(CODES, 1) == CODES[3:6]


def test_rotation_wraps_around_after_the_real_total_group_count():
    # Deliberately computed from len(CODES), not a hardcoded group
    # count — DEFENSE_RELEVANT_NAICS has grown once already (6 -> 14
    # codes, 2026-09) after a real coverage gap was found, and a
    # magic number here would have silently stopped testing the real
    # wraparound the moment that list changed size again.
    import math
    total_groups = math.ceil(len(CODES) / NAICS_GROUP_SIZE)
    assert get_naics_group(CODES, total_groups) == get_naics_group(CODES, 0)


def test_wraparound_still_works_with_a_fixed_small_list():
    # The general wraparound LOGIC, independent of however many real
    # codes DEFENSE_RELEVANT_NAICS happens to hold right now.
    codes = ["A", "B", "C", "D", "E", "F"]  # 6 codes, group size 3 -> exactly 2 groups
    assert get_naics_group(codes, 2) == get_naics_group(codes, 0)


def test_successive_indices_actually_differ():
    # the real bug this whole feature fixes: successive rotation
    # indices must produce DIFFERENT groups, not the same static slice
    group_0 = get_naics_group(CODES, 0)
    group_1 = get_naics_group(CODES, 1)
    assert group_0 != group_1


def test_empty_code_list_returns_empty():
    assert get_naics_group([], 0) == []
    assert get_naics_group([], 5) == []


def test_fewer_codes_than_group_size():
    assert get_naics_group(["A", "B"], 0) == ["A", "B"]
    assert get_naics_group(["A", "B"], 1) == ["A", "B"]  # only 1 group exists, wraps to itself


def test_uneven_split_last_group_is_partial():
    codes = ["A", "B", "C", "D", "E"]  # 5 codes, group size 3 -> groups of [A,B,C] and [D,E]
    assert get_naics_group(codes, 0) == ["A", "B", "C"]
    assert get_naics_group(codes, 1) == ["D", "E"]
    assert get_naics_group(codes, 2) == ["A", "B", "C"]  # wraps


def test_defense_relevant_naics_now_covers_at_least_fourteen_codes():
    """
    Pins the real coverage-gap fix: this used to be 6 codes, meaning
    only 2 rotation groups ever existed and most capability areas
    (Naval, Land Systems, Comms, Aerospace engines, Space, Cyber, MRO)
    could never surface real SAM.gov data no matter how good their
    taxonomy_naics_mapping rows were. Guards against it silently
    shrinking back.
    """
    assert len(DEFENSE_RELEVANT_NAICS) >= 14
