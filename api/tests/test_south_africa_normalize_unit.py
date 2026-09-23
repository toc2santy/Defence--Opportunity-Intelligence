"""
Unit tests for app.south_africa_normalize — no network, no database.

The ARMSCOR release fixture below is a trimmed copy of a real OCDS
release captured live from ocds-api.etenders.gov.za during research —
the field shape (tender.procuringEntity, tender.tenderPeriod.endDate,
tender.contactPerson, tender.documents[0].url) all come from the live
API response, not invented structure.
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.south_africa_normalize import (
    SOUTH_AFRICA_DEFENCE_ORG_CODE,
    is_defence_organisation,
    normalize_batch,
    normalize_release,
)


REAL_ARMSCOR_RELEASE = {
    "ocid": "ocds-9t57fa-166713",
    "date": "2026-08-24T00:00:00Z",
    "tender": {
        "id": "166713",
        "title": "RR83712/26-27",
        "status": "active",
        "category": "Services: Building",
        "description": "Appointment of survey, design, supply, commission and verification "
                        "of a secure tactical communications relay upgrade at Armscor Dockyard",
        "tenderPeriod": {"startDate": "2026-08-24T00:00:00Z", "endDate": "2026-09-08T11:00:00Z"},
        "procuringEntity": {"id": "19", "name": "ARMSCOR"},
        "documents": [{"url": "https://www.etenders.gov.za/home/Download?blobName=abc.pdf"}],
        "contactPerson": {
            "name": "Procurementdy@armscordy.co.za",
            "email": "Procurementdy@armscordy.co.za",
            "telephoneNumber": "021-787-3177",
        },
    },
    "buyer": {"id": "19", "name": "ARMSCOR"},
}

NON_DEFENCE_RELEASE = {
    "ocid": "ocds-9t57fa-999999",
    "date": "2026-08-20T00:00:00Z",
    "tender": {
        "id": "999999",
        "title": "RFQ/SAEON/ELWANDLE/129/2026",
        "status": "active",
        "description": "Provision of BVLOS survey services for marine research",
        "tenderPeriod": {"endDate": "2026-09-01T00:00:00Z"},
        "procuringEntity": {"name": "National Research Foundation"},
    },
    "buyer": {"name": "National Research Foundation"},
}

FACILITIES_WORKS_ARMSCOR_RELEASE = {
    "ocid": "ocds-9t57fa-111111",
    "date": "2026-08-20T00:00:00Z",
    "tender": {
        "id": "111111",
        "title": "RR90000/26-27",
        "status": "active",
        "description": "Repair and maintenance of roof and ceiling at Armscor Dockyard housing block",
        "tenderPeriod": {"endDate": "2026-09-05T00:00:00Z"},
        "procuringEntity": {"name": "ARMSCOR"},
    },
    "buyer": {"name": "ARMSCOR"},
}

NO_TENDER_ID_RELEASE = {
    "ocid": "",
    "tender": {"title": "Untitled", "status": "active", "procuringEntity": {"name": "ARMSCOR"}},
}


def test_is_defence_organisation_matches_armscor():
    assert is_defence_organisation("ARMSCOR") is True
    assert is_defence_organisation("Department of Defence") is True
    assert is_defence_organisation("National Research Foundation") is False
    assert is_defence_organisation(None) is False


def test_is_defence_organisation_acronym_word_boundary():
    assert is_defence_organisation("SANDF") is True
    assert is_defence_organisation("SANDFORD MUNICIPALITY") is False  # not a real body, but proves no substring leak


def test_normalize_release_maps_real_armscor_fields():
    record = normalize_release(REAL_ARMSCOR_RELEASE)
    assert record["external_ref"] == "166713"
    assert record["country"] == "South Africa"
    assert record["organization_name"] == "ARMSCOR"
    assert record["stage"] == "rfp_issued"
    assert record["classification_code"] == SOUTH_AFRICA_DEFENCE_ORG_CODE
    assert record["response_deadline"] == "2026-09-08T11:00:00Z"
    assert record["ui_link"] == "https://www.etenders.gov.za/home/Download?blobName=abc.pdf"
    assert record["contact_email"] == "Procurementdy@armscordy.co.za"
    assert record["contact_phone"] == "021-787-3177"


def test_normalize_release_requires_tender_id():
    import pytest
    with pytest.raises(ValueError):
        normalize_release(NO_TENDER_ID_RELEASE)


def test_normalize_batch_keeps_defence_buyer_capability_procurement():
    normalized, failures = normalize_batch([REAL_ARMSCOR_RELEASE])
    assert len(normalized) == 1
    assert failures == []


def test_normalize_batch_drops_non_defence_buyer():
    normalized, failures = normalize_batch([NON_DEFENCE_RELEASE])
    assert normalized == []
    assert failures == []  # correctly parsed, just not defence-relevant — not a failure


def test_normalize_batch_drops_facilities_works_even_from_armscor():
    normalized, failures = normalize_batch([FACILITIES_WORKS_ARMSCOR_RELEASE])
    assert normalized == []
    assert failures == []


def test_normalize_batch_mixed_input():
    normalized, failures = normalize_batch([
        REAL_ARMSCOR_RELEASE, NON_DEFENCE_RELEASE, FACILITIES_WORKS_ARMSCOR_RELEASE, NO_TENDER_ID_RELEASE,
    ])
    assert len(normalized) == 1
    assert normalized[0]["external_ref"] == "166713"
    assert len(failures) == 1


# --- tender statuses that are valid but not biddable -----------------

def test_cancelled_tender_is_skipped_not_reported_as_a_failure():
    """
    Live behaviour that this pins: in the first working run after the
    fetch was repaired, South Africa returned 94 releases over 30
    days, exactly two were defence-relevant, and both were cancelled.
    They were being recorded as parse FAILURES, which reads as a
    broken normalizer rather than as tenders that were called off —
    and contradicted this function's own docstring.
    """
    release = copy.deepcopy(REAL_ARMSCOR_RELEASE)
    release["tender"]["status"] = "cancelled"
    normalized, failures = normalize_batch([release])
    assert normalized == []
    assert failures == [], "a cancelled tender is a skip, not a failure"


def test_withdrawn_and_unsuccessful_are_skipped_too():
    for status in ("withdrawn", "unsuccessful"):
        release = copy.deepcopy(REAL_ARMSCOR_RELEASE)
        release["tender"]["status"] = status
        normalized, failures = normalize_batch([release])
        assert normalized == [], status
        assert failures == [], status


def test_a_status_outside_the_ocds_codelist_still_fails_loudly():
    """
    Skipping the known-but-unbiddable statuses must not turn into
    swallowing a genuinely unexpected one — that would hide a real
    change in the feed.
    """
    release = copy.deepcopy(REAL_ARMSCOR_RELEASE)
    release["tender"]["status"] = "somethingNewEntirely"
    normalized, failures = normalize_batch([release])
    assert normalized == []
    assert len(failures) == 1
    assert "Unmapped OCDS tender status" in failures[0]["error"]


def test_denel_is_recognised_as_a_defence_buyer():
    """
    DENEL is South Africa's state-owned defence prime (artillery,
    missiles, armoured vehicles, aerostructures) — the closest
    equivalent to the Indian DPSUs cppp_india_normalize already
    covers. It was missing from the phrase list entirely, which is how
    a live 90-day window whose raw feed contained "DENEL (Pty) Ltd" as
    a publishing buyer produced zero defence-relevant releases. Found
    by checking the feed's distinct buyer names against this filter
    rather than trusting the filter's own output.
    """
    assert is_defence_organisation("DENEL (Pty) Ltd")
    assert is_defence_organisation("Denel Aeronautics")
    # Still no false positives on the utilities and agencies that
    # dominate this feed.
    assert not is_defence_organisation("ESKOM")
    assert not is_defence_organisation("Airports Company of South Africa")


def test_a_real_denel_release_survives_the_whole_filter_chain():
    release = copy.deepcopy(REAL_ARMSCOR_RELEASE)
    release["tender"]["procuringEntity"] = {"name": "DENEL (Pty) Ltd"}
    release["buyer"] = {"name": "DENEL (Pty) Ltd"}
    normalized, failures = normalize_batch([release])
    assert failures == []
    assert len(normalized) == 1
    assert normalized[0]["organization_name"] == "DENEL (Pty) Ltd"
