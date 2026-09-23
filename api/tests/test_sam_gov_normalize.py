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


def test_normalize_constructs_ui_link_when_sam_gov_omits_it():
    # Confirmed against live ingested data: SAM.gov's own `uiLink`
    # field is absent on the large majority of real records (only
    # ~10% carried it in a live check), with no pattern by notice
    # type/stage — but every record that DID carry it followed this
    # exact URL shape, keyed on noticeId (verified 44/44 against real
    # stored data). So a genuinely-missing uiLink (actual None, not
    # GSA's own docs' string "null" quirk — see the sparse-fields test
    # below) gets this reconstructed rather than left blank.
    record = dict(REAL_EXAMPLE_1)
    record["uiLink"] = None
    result = normalize_opportunity(record)
    assert result["ui_link"] == "https://sam.gov/workspace/contract/opp/5b345bbb7127b91a3ad577b203fc6f68/view"


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


# --- contact + address, added because this was previously a total gap ---

def test_pointofcontact_is_extracted_preferring_primary():
    raw = dict(REAL_EXAMPLE_1)
    raw["pointOfContact"] = [
        {"type": "secondary", "email": "second@gsa.gov", "phone": "1112223333", "fullName": "Sec Ondary"},
        {"type": "primary", "email": "jesse.jones@gsa.gov", "phone": "2174941263", "fullName": "Jesse L. Jones"},
    ]
    r = normalize_opportunity(raw)
    assert r["contact_name"] == "Jesse L. Jones"
    assert r["contact_email"] == "jesse.jones@gsa.gov"
    assert r["contact_phone"] == "2174941263"


def test_single_contact_with_no_type_is_still_used():
    raw = dict(REAL_EXAMPLE_1)
    raw["pointOfContact"] = [{"email": "only@gsa.gov", "fullName": "Only One"}]
    r = normalize_opportunity(raw)
    assert r["contact_name"] == "Only One"


def test_no_pointofcontact_gives_none_not_an_error():
    raw = dict(REAL_EXAMPLE_1)
    raw.pop("pointOfContact", None)
    r = normalize_opportunity(raw)
    assert r["contact_name"] is None
    assert r["contact_email"] is None


def test_office_address_is_used_not_place_of_performance():
    """
    officeAddress is the CONTRACTING OFFICE's address — where a
    supplier would write to reach this office. placeOfPerformance is
    a different field (where the awarded work happens, often a
    different city entirely) and must never be used here.
    """
    raw = dict(REAL_EXAMPLE_1)
    raw["officeAddress"] = {"zipcode": "60604", "city": "CHICAGO", "countryCode": "USA", "state": "IL"}
    raw["placeOfPerformance"] = {
        "streetAddress": "517 E Wisconsin Ave",
        "city": {"code": "53000", "name": "Milwaukee"},
        "state": {"code": "WI"}, "zip": "53202", "country": {"code": "USA"},
    }
    r = normalize_opportunity(raw)
    assert "CHICAGO" in r["contact_address"]
    assert "IL" in r["contact_address"]
    assert "Milwaukee" not in r["contact_address"]


def test_no_office_address_gives_none():
    raw = dict(REAL_EXAMPLE_1)
    raw.pop("officeAddress", None)
    r = normalize_opportunity(raw)
    assert r["contact_address"] is None


# --- award winner extraction, added after Report Intel review ---------

def test_award_winner_extracted_from_real_gsa_example_1():
    assert normalize_opportunity(REAL_EXAMPLE_1)["winner_name"] == "D.G. Beyer, Inc."


def test_award_object_present_but_no_awardee_key_at_all():
    """
    GSA's own 'Example 2' — award.date/number/amount populated but
    the awardee sub-object is entirely ABSENT, not null. This is the
    real shape that has to be handled, not a hypothetical edge case.
    """
    assert normalize_opportunity(REAL_EXAMPLE_2)["winner_name"] is None


def test_no_award_object_at_all():
    raw = dict(REAL_EXAMPLE_1)
    raw.pop("award", None)
    assert normalize_opportunity(raw)["winner_name"] is None


def test_secondary_contact_email_is_captured():
    raw = dict(REAL_EXAMPLE_1)
    raw["pointOfContact"] = [
        {"type": "primary", "email": "jesse.jones@gsa.gov", "phone": "2174941263", "fullName": "Jesse L. Jones"},
        {"type": "secondary", "email": "backup@gsa.gov", "phone": "2175551234", "fullName": "Pat Backup"},
    ]
    r = normalize_opportunity(raw)
    assert r["contact_email"] == "jesse.jones@gsa.gov"
    assert r["contact_email_secondary"] == "backup@gsa.gov"


def test_no_secondary_contact_gives_none_not_an_error():
    r = normalize_opportunity(REAL_EXAMPLE_1)  # only one contact, no type "secondary"
    assert r["contact_email_secondary"] is None


def test_award_present_but_awardee_name_blank():
    raw = dict(REAL_EXAMPLE_1)
    raw["award"] = {"date": "2026-01-01", "awardee": {"ueiSAM": "X"}}
    assert normalize_opportunity(raw)["winner_name"] is None
