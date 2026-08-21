"""
Unit tests for app.sam_gov_normalize — no network, no database.

The fixtures below are copied verbatim from GSA's own published
documentation (open.gsa.gov/api/get-opportunities-public-api,
"Example 1" and "Example 2"), not invented — so passing these
tests means the parser genuinely handles SAM.gov's real response
shape, including its documented inconsistencies (e.g. Example 2
has several null fields that Example 1 populates).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sam_gov_normalize import normalize_opportunity, normalize_batch


# Verbatim from GSA docs, "Example 1: Search by award type"
REAL_EXAMPLE_1 = {
    "noticeId": "5b345bbb7127b91a3ad577b203fc6f68",
    "title": "Historic Office Renovation ",
    "solicitationNumber": " 47PF0018R0023 ",
    "department": "GENERAL SERVICES ADMINISTRATION",
    "subTier": "PUBLIC BUILDINGS SERVICE",
    "office": "PBS R5",
    "postedDate": "2018-05-04",
    "type": "Award Notice",
    "baseType": "Combined Synopsis/Solicitation",
    "archiveType": "manual",
    "archiveDate": None,
    "typeOfSetAsideDescription": None,
    "typeOfSetAside": None,
    "responseDeadLine": None,
    "naicsCode": "236220",
    "classificationCode": "Z",
    "active": "Yes",
    "award": {
        "date": "2018-05-04",
        "number": "47PF0018C0066",
        "amount": "800620",
        "awardee": {"name": "D.G. Beyer, Inc.", "ueiSAM": "025114695AST"},
    },
    "pointOfContact": [
        {"fax": None, "type": "primary", "email": "jesse.jones@gsa.gov",
         "phone": "2174941263", "title": "Contracting Officer ", "fullName": "Jesse L. Jones"}
    ],
    "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=5b345bbb7127b91a3ad577b203fc6f68",
    "organizationType": "OFFICE",
    "officeAddress": {"zipcode": "60604", "city": "CHICAGO", "countryCode": "USA", "state": "IL"},
    "placeOfPerformance": {
        "streetAddress": "517 E Wisconsin Ave",
        "city": {"code": "53000", "name": "Milwaukee"},
        "state": {"code": "WI"}, "zip": "53202", "country": {"code": "USA"},
    },
    "additionalInfoLink": None,
    "uiLink": "https://beta.sam.gov/opp/5b345bbb7127b91a3ad577b203fc6f68/view",
}

# Verbatim from GSA docs, "Example 2: Updated v2 Endpoint with FH Information"
# Note this example has naicsCode=None, pointOfContact=None,
# placeOfPerformance=None — deliberately sparse, per GSA's own docs.
REAL_EXAMPLE_2 = {
    "noticeId": "ff826a59eac743c4a1a07ff5e0cf3e3a",
    "title": "Test-Award notice-V2 27",
    "solicitationNumber": "test-123456789",
    "fullParentPathName": "GENERAL SERVICES ADMINISTRATION.FEDERAL ACQUISITION SERVICE.GSA/FAS CENTER FOR IT SCHEDULE PROG",
    "fullParentPathCode": "047.4732.47QTCA",
    "postedDate": "2020-07-02",
    "type": "Award Notice",
    "baseType": "Award Notice",
    "archiveType": "autocustom",
    "archiveDate": "2021-01-02",
    "typeOfSetAsideDescription": None,
    "typeOfSetAside": None,
    "responseDeadLine": None,
    "naicsCode": None,
    "classificationCode": None,
    "active": "Yes",
    "award": {"date": "2020-12-01", "number": "4376487348950", "amount": "350567.00"},
    "pointOfContact": None,
    "description": "null",
    "organizationType": "OFFICE",
    "officeAddress": {"zipcode": "20405", "city": "WASHINGTON", "countryCode": "USA", "state": "DC"},
    "placeOfPerformance": None,
    "additionalInfoLink": None,
    "uiLink": "null",
    "resourceLinks": None,
}


def test_normalize_real_example_1():
    result = normalize_opportunity(REAL_EXAMPLE_1)
    assert result["external_ref"] == "5b345bbb7127b91a3ad577b203fc6f68"
    assert result["name"] == "Historic Office Renovation"  # trimmed
    assert result["country"] == "United States"
    assert result["naics_code"] == "236220"
    assert result["posted_date"] == "2018-05-04"
    assert result["stage"] == "contract_awarded"  # type "Award Notice" (current status) takes priority over baseType
    assert result["ui_link"] == "https://beta.sam.gov/opp/5b345bbb7127b91a3ad577b203fc6f68/view"


def test_normalize_real_example_2_has_null_set_aside_matching_gsas_own_docs():
    # GSA's own documented example genuinely shows both fields as
    # null — this confirms the parser handles the "no restriction"
    # case correctly, which is the MORE common real case in practice.
    result = normalize_opportunity(REAL_EXAMPLE_2)
    assert result["set_aside_code"] is None
    assert result["set_aside_description"] is None


def test_normalize_extracts_a_real_populated_set_aside():
    # typeOfSetAside/typeOfSetAsideDescription aren't shown populated
    # in GSA's own example (see above), so this constructs a
    # realistic populated case using SBA — a real, publicly
    # documented SAM.gov set-aside code ("Total Small Business
    # Set-Aside") — layered onto the same real example structure,
    # not an invented field name or format.
    record = dict(REAL_EXAMPLE_2)
    record["typeOfSetAside"] = "SBA"
    record["typeOfSetAsideDescription"] = "Total Small Business Set-Aside"
    result = normalize_opportunity(record)
    assert result["set_aside_code"] == "SBA"
    assert result["set_aside_description"] == "Total Small Business Set-Aside"


def test_normalize_real_example_2_handles_sparse_fields():
    # This example has many nulls per GSA's own docs — the parser
    # must not crash on missing naicsCode, pointOfContact, etc.
    result = normalize_opportunity(REAL_EXAMPLE_2)
    assert result["external_ref"] == "ff826a59eac743c4a1a07ff5e0cf3e3a"
    assert result["name"] == "Test-Award notice-V2 27"
    assert result["naics_code"] is None
    assert result["organization_name"] == (
        "GENERAL SERVICES ADMINISTRATION.FEDERAL ACQUISITION SERVICE.GSA/FAS CENTER FOR IT SCHEDULE PROG"
    )
    assert result["stage"] == "contract_awarded"  # type "Award Notice"


def test_normalize_falls_back_gracefully_on_unknown_type():
    record = dict(REAL_EXAMPLE_1)
    record["type"] = "Some Brand New SAM.gov Type We've Never Seen"
    record["baseType"] = "Also New"
    result = normalize_opportunity(record)
    assert result["stage"] == "requirement_defined"  # safe fallback, not a crash


def test_normalize_raises_on_record_with_no_identifiable_id():
    record = {"title": "A record with no noticeId or solicitationNumber at all"}
    try:
        normalize_opportunity(record)
        assert False, "expected ValueError for a record with no dedup key"
    except ValueError:
        pass


def test_normalize_batch_isolates_failures():
    good_record = REAL_EXAMPLE_1
    bad_record = {"title": "No ID here"}
    normalized, failures = normalize_batch([good_record, bad_record, REAL_EXAMPLE_2])

    assert len(normalized) == 2, "the two valid records should still succeed"
    assert len(failures) == 1, "the one bad record should be isolated, not abort the batch"
    assert failures[0]["title"] == "No ID here"


def test_title_whitespace_is_trimmed():
    # SAM.gov's own example data has leading/trailing whitespace in
    # solicitationNumber (" 47PF0018R0023 ") and title
    # ("Historic Office Renovation ") — a real vendor data quirk,
    # not something we're inventing to test against.
    result = normalize_opportunity(REAL_EXAMPLE_1)
    assert result["name"] == result["name"].strip()
    assert not result["name"].endswith(" ")
