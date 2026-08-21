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

from app.uk_ft_normalize import normalize_release, normalize_batch, is_defense_relevant_cpv


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
