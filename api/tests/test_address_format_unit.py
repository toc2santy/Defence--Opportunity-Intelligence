"""Unit tests for app.address_format — no network, no database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.address_format import format_address


def test_all_parts_joined_in_order():
    assert format_address("123 Main St", "Springfield", "IL", "62701", "United States") == \
        "123 Main St, Springfield, IL, 62701, United States"


def test_missing_middle_part_leaves_no_gap():
    assert format_address(street="123 Main St", country="United States") == "123 Main St, United States"


def test_whitespace_only_part_is_treated_as_missing():
    assert format_address(street="  ", locality="Springfield") == "Springfield"


def test_nothing_published_gives_none_not_empty_string():
    assert format_address() is None
    assert format_address(street="", locality=None) is None
