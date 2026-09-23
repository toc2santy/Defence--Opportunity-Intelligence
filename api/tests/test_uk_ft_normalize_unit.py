"""
Unit tests for app.uk_ft_normalize — no network, no database.
Uses the REAL example response from GOV.UK's own API documentation
(find-tender.service.gov.uk/apidocumentation/1.0/GET-ocdsReleasePackages),
not invented data — every assertion here was run standalone before
being committed to this file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.uk_ft_normalize import (
    normalize_release, normalize_batch, is_defense_relevant_cpv, extract_winners, extract_contact,
    extract_set_aside,
)


# Verbatim from GOV.UK's own documented example response
REAL_EXAMPLE = {
    "ocid": "ocds-h6vhtk-0026ef-integration",
    "id": "001060-2020",
    "date": "2020-12-08T11:07:45Z",
    "tag": ["planning"],
    "description": "Sample additional information",
    "initiationType": "tender",
    "tender": {
        "id": "123456",
        "title": "Sample title",
        "status": "planned",
        "classification": {
            "scheme": "CPV", "id": "03000000",
            "description": "Agricultural, farming, fishing, forestry and related products",
        },
        "mainProcurementCategory": "goods",
    },
    "parties": [
        {"name": "Buyer name", "id": "1", "roles": ["buyer", "centralPurchasingBody"]}
    ],
    "buyer": {"id": "1", "name": "Buyer name"},
    "language": "en",
}


def test_normalize_real_example():
    result = normalize_release(REAL_EXAMPLE)
    assert result["external_ref"] == "001060-2020"
    assert result["name"] == "Sample title"
    assert result["country"] == "United Kingdom"
    assert result["organization_name"] == "Buyer name"
    assert result["stage"] == "early_concept"  # tag[0] "planning"
    assert result["classification_code"] == "03000000"
    assert result["classification_scheme"] == "CPV"
    assert result["ui_link"] == "https://www.find-tender.service.gov.uk/Notice/001060-2020"


def test_normalize_falls_back_to_parties_array_when_buyer_name_missing():
    record = dict(REAL_EXAMPLE)
    record["buyer"] = {"id": "1"}  # no name directly on buyer
    result = normalize_release(record)
    assert result["organization_name"] == "Buyer name"  # recovered from parties[].roles


def test_normalize_raises_on_record_with_no_identifiable_id():
    try:
        normalize_release({"tender": {"title": "No ID here"}})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_stage_tag_award_maps_to_contract_awarded():
    record = dict(REAL_EXAMPLE)
    record["tag"] = ["award"]
    assert normalize_release(record)["stage"] == "contract_awarded"


def test_stage_tag_tender_maps_to_rfp_issued():
    record = dict(REAL_EXAMPLE)
    record["tag"] = ["tender"]
    assert normalize_release(record)["stage"] == "rfp_issued"


def test_unknown_stage_tag_falls_back_safely():
    record = dict(REAL_EXAMPLE)
    record["tag"] = ["some_new_tag_we_have_never_seen"]
    assert normalize_release(record)["stage"] == "requirement_defined"


# ---------------------------------------------------------------
# CPV classification — verified against real, independently
# cross-referenced procurement classification data, not guessed.
# ---------------------------------------------------------------
def test_defense_division_35_is_relevant():
    assert is_defense_relevant_cpv("35000000") is True
    assert is_defense_relevant_cpv("35120000") is True  # surveillance and security systems
    assert is_defense_relevant_cpv("35410000") is True  # armoured military vehicles


def test_specific_5x_repair_codes_are_relevant():
    assert is_defense_relevant_cpv("50630000") is True  # military vehicle repair
    assert is_defense_relevant_cpv("50640000") is True  # warship repair
    assert is_defense_relevant_cpv("50660000") is True  # military electronic systems repair


def test_non_defense_codes_are_not_relevant():
    assert is_defense_relevant_cpv("03000000") is False  # agricultural (the real doc example)
    assert is_defense_relevant_cpv("45000000") is False  # construction
    assert is_defense_relevant_cpv("50100000") is False  # generic vehicle repair, not military


def test_missing_cpv_code_is_not_relevant():
    assert is_defense_relevant_cpv(None) is False
    assert is_defense_relevant_cpv("") is False


def test_batch_filters_out_non_defense_relevant_the_real_doc_example_is_agricultural():
    normalized, failures = normalize_batch([REAL_EXAMPLE])
    assert normalized == []  # correctly filtered out — it's agricultural, not defense
    assert failures == []  # not malformed, just not relevant


def test_batch_keeps_defense_relevant_records():
    defense_record = dict(REAL_EXAMPLE)
    defense_record["tender"] = dict(REAL_EXAMPLE["tender"])
    defense_record["tender"]["classification"] = {
        "scheme": "CPV", "id": "35120000", "description": "Surveillance and security systems and devices",
    }
    normalized, failures = normalize_batch([defense_record])
    assert len(normalized) == 1
    assert normalized[0]["classification_code"] == "35120000"


def test_batch_isolates_malformed_records():
    good = dict(REAL_EXAMPLE)
    good["tender"] = dict(REAL_EXAMPLE["tender"])
    good["tender"]["classification"] = {"scheme": "CPV", "id": "35300000"}
    bad = {"tender": {"title": "No ID"}}
    normalized, failures = normalize_batch([good, bad])
    assert len(normalized) == 1
    assert len(failures) == 1


# --- OEM Intelligence: winner extraction ---------------------------
# The fixture below is a trimmed copy of a real live award-release
# 'parties' entry (captured earlier this session) — a supplier party
# carries its own name and country directly, unlike TED's parallel
# arrays, so there's no alignment risk to guard against here.

REAL_SUPPLIER_PARTY = {
    "name": "Corporate Travel Management (North) Limited",
    "id": "GB-FTS-186047",
    "address": {
        "streetAddress": "Shire House, Humboldt Street",
        "locality": "Bradford",
        "countryName": "United Kingdom",
    },
    "roles": ["supplier"],
}


def test_extract_winners_from_supplier_party():
    release = {"parties": [
        {"name": "British Tourist Authority", "roles": ["buyer"]},
        REAL_SUPPLIER_PARTY,
    ]}
    assert extract_winners(release) == [
        {"name": "Corporate Travel Management (North) Limited", "country": "United Kingdom"},
    ]


def test_extract_winners_multiple_suppliers():
    release = {"parties": [
        REAL_SUPPLIER_PARTY,
        {"name": "TBR Global Limited", "roles": ["supplier"], "address": {"countryName": "United Kingdom"}},
    ]}
    winners = extract_winners(release)
    assert [w["name"] for w in winners] == [
        "Corporate Travel Management (North) Limited", "TBR Global Limited",
    ]


def test_extract_winners_ignores_non_supplier_roles():
    release = {"parties": [{"name": "Some Reviewer", "roles": ["reviewBody"]}]}
    assert extract_winners(release) == []


def test_extract_winners_handles_missing_address():
    release = {"parties": [{"name": "No Address Ltd", "roles": ["supplier"]}]}
    assert extract_winners(release) == [{"name": "No Address Ltd", "country": None}]


def test_extract_winners_empty_when_no_parties():
    assert extract_winners({}) == []


def test_normalize_release_includes_winners():
    release = dict(REAL_EXAMPLE)
    release["parties"] = list(REAL_EXAMPLE["parties"]) + [REAL_SUPPLIER_PARTY]
    record = normalize_release(release)
    assert record["winners"] == [
        {"name": "Corporate Travel Management (North) Limited", "country": "United Kingdom"},
    ]


def test_normalize_release_winners_empty_when_no_supplier():
    assert normalize_release(REAL_EXAMPLE)["winners"] == []


# --- contact address, added alongside db/migrations/029 -------------

def test_buyer_address_is_read_from_the_same_ocds_shape_as_supplier_address():
    """
    The buyer party's `address` uses the identical OCDS Address object
    already confirmed live on SUPPLIER parties (see
    REAL_SUPPLIER_PARTY above) — same schema, different role.
    """
    release = {"parties": [
        {"name": "Bradford Council", "roles": ["buyer"],
         "address": {"streetAddress": "Britannia House", "locality": "Bradford",
                      "region": "West Yorkshire", "postalCode": "BD1 1HX", "countryName": "United Kingdom"},
         "contactPoint": {"name": "Procurement Team", "email": "procurement@bradford.gov.uk"}},
    ]}
    contact = extract_contact(release)
    assert contact["contact_name"] == "Procurement Team"
    assert "Bradford" in contact["contact_address"]
    assert "BD1 1HX" in contact["contact_address"]
    assert "United Kingdom" in contact["contact_address"]


def test_no_buyer_address_gives_none_not_an_error():
    release = {"parties": [{"name": "X", "roles": ["buyer"], "contactPoint": {"name": "Y"}}]}
    contact = extract_contact(release)
    assert contact["contact_address"] is None


def test_no_buyer_party_at_all_gives_a_fully_empty_contact():
    contact = extract_contact({"parties": []})
    assert contact == {"contact_name": None, "contact_email": None, "contact_phone": None, "contact_address": None}


# --- eligibility: reservedParticipation, Eligibility Phase 1 follow-up (2026-09) ---
# Real field path confirmed by a live scan of ~1000 real Find a
# Tender releases: tender.otherRequirements.reservedParticipation,
# NOT a top-level reservedParticipationLocation field (never observed
# live despite this project's own earlier note naming it). One real
# example found live: release 041633-2026 carries ["shelteredWorkshop"].

def test_extract_set_aside_reads_the_real_live_confirmed_value():
    tender = {"otherRequirements": {"reservedParticipation": ["shelteredWorkshop"]}}
    code, description = extract_set_aside(tender)
    assert code == "shelteredWorkshop"
    assert description == "Reserved for sheltered workshops / supported businesses"


def test_extract_set_aside_unknown_code_shows_raw_value_not_a_guessed_label():
    tender = {"otherRequirements": {"reservedParticipation": ["someCodeWeHaveNeverSeen"]}}
    code, description = extract_set_aside(tender)
    assert code == "someCodeWeHaveNeverSeen"
    assert description == "Reserved participation: someCodeWeHaveNeverSeen"


def test_extract_set_aside_none_when_field_absent():
    assert extract_set_aside({}) == (None, None)
    assert extract_set_aside({"otherRequirements": {}}) == (None, None)
    assert extract_set_aside({"otherRequirements": {"reservedParticipation": []}}) == (None, None)


def test_normalize_release_includes_set_aside_fields():
    release = dict(REAL_EXAMPLE)
    release["tender"] = dict(REAL_EXAMPLE["tender"])
    release["tender"]["otherRequirements"] = {"reservedParticipation": ["shelteredWorkshop"]}
    record = normalize_release(release)
    assert record["set_aside_code"] == "shelteredWorkshop"
    assert record["set_aside_description"] == "Reserved for sheltered workshops / supported businesses"


def test_normalize_release_set_aside_none_when_not_reserved():
    record = normalize_release(REAL_EXAMPLE)
    assert record["set_aside_code"] is None
    assert record["set_aside_description"] is None
