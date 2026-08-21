"""
Proves NAICS rotation genuinely advances through the real live
endpoint — not just that the pure math is correct in isolation.
Requires a real SAM_GOV_API_KEY, same gating pattern as
test_sam_gov_live.py, and shares that file's warning: don't run
this as part of a full sweep alongside test_rate_limiting.py, since
both exhaust /ingestion/sam-gov/run's shared 5/hour budget.

To run:
    SAM_GOV_API_KEY=your_real_key pytest tests/test_naics_rotation_live.py -v -s
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAM_GOV_API_KEY"),
    reason="SAM_GOV_API_KEY not set — skipping live rotation test.",
)


def test_two_consecutive_unparameterized_runs_use_different_naics_groups(client, auth_headers):
    status_before = client.get("/ingestion/scheduler-status", headers=auth_headers)
    assert status_before.status_code == 200
    rotation_before = status_before.json()["rotation"]
    print(f"\nRotation before: index={rotation_before['current_rotation_index']}, "
          f"next={rotation_before['naics_codes_next_run']}")

    run_1 = client.post("/ingestion/sam-gov/run", headers=auth_headers, json={})
    assert run_1.status_code == 200
    codes_1 = run_1.json()["naics_codes_queried"]
    assert run_1.json()["used_automatic_rotation"] is True
    print(f"Run 1 used: {codes_1}")

    status_after_1 = client.get("/ingestion/scheduler-status", headers=auth_headers)
    rotation_after_1 = status_after_1.json()["rotation"]
    print(f"Rotation after run 1: index={rotation_after_1['current_rotation_index']}, "
          f"next={rotation_after_1['naics_codes_next_run']}")

    # the index must have actually moved
    assert rotation_after_1["current_rotation_index"] != rotation_before["current_rotation_index"], (
        "rotation index did not advance after a real ingestion run"
    )
    # and the codes for the NEXT run must differ from what run 1 just used
    assert rotation_after_1["naics_codes_next_run"] != codes_1, (
        "next run would use the exact same NAICS codes as the last run — rotation isn't working"
    )
