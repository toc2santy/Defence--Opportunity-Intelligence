"""
Golden cases: REAL payloads captured from the sources (api/tests/golden/source_payloads.json),
each with what the source itself says the answer is. Run through the real normalizers
offline, so a regression in how a source's status is understood fails here instead of
showing a customer an awarded tender as open.

Every case was either reported by a user or is the control for one:
  - SECOP II awarded tender whose `fase` still says "offers" (stored as open for months)
  - SECOP II genuinely-open control, and a cancelled one
  - two Paraguay "Adjudicada" tenders (feminine form was never matched)
The matching live comparison against the sources runs weekly (app/source_reconcile.py).
Add a case here whenever a data-meaning bug is found.
"""

import json
from pathlib import Path

import pytest

CASES = json.loads((Path(__file__).parent / "golden" / "source_payloads.json").read_text())["cases"]


def _normalize(case):
    if case["source"] == "secop":
        from app.colombia_normalize import normalize_row
        return normalize_row(case["raw"])
    if case["source"] == "paraguay":
        from app.paraguay_normalize import normalize_record
        return normalize_record(case["raw"])
    raise AssertionError(f"unknown source {case['source']}")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case):
    got = _normalize(case)
    assert got is not None, f"{case['id']} was filtered out entirely ({case['why']})"
    for field, expected in case["expect"].items():
        assert got.get(field) == expected, f"{case['id']}: {field} = {got.get(field)!r}, source says {expected!r} — {case['why']}"


def test_there_is_a_control_case_that_must_stay_open():
    assert any(c["expect"].get("stage") == "rfp_issued" for c in CASES), (
        "keep at least one genuinely-open case, or the fix could silently turn everything into 'awarded'"
    )
