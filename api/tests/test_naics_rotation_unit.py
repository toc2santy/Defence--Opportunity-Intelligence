"""
Unit tests for app.sam_gov_normalize.get_naics_group — no DB, no
network. Every assertion here was run standalone before being
committed to this file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sam_gov_normalize import get_naics_group, DEFENSE_RELEVANT_NAICS, NAICS_GROUP_SIZE


CODES = list(DEFENSE_RELEVANT_NAICS.keys())  # currently 6 real codes


def test_first_group_is_first_n_codes():
    assert get_naics_group(CODES, 0) == CODES[:3]


def test_second_group_is_next_n_codes():
    assert get_naics_group(CODES, 1) == CODES[3:6]


def test_rotation_wraps_around_to_first_group():
    # with 6 codes and group size 3, there are exactly 2 groups —
    # index 2 should wrap back to the same as index 0
    assert get_naics_group(CODES, 2) == get_naics_group(CODES, 0)


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
