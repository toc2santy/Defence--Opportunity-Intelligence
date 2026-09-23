"""
Unit tests for app.canada_buys_normalize — no network, no database.

Every fixture value below is copied from a real row of the live
CanadaBuys open-tenders CSV: the bilingual column names, the
asterisk-prefixed newline-separated UNSPSC encoding, the real
solicitation-number formats (W6399-27-TR21), and real tender titles
including the Victoria-class submarine spares and the CFB Halifax
construction source list.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.canada_buys_normalize import (
    canadabuys_portal_url,
    normalize_award_batch,
    normalize_award_row,
    COL_CLOSING,
    COL_CONTRACTING_ENTITY,
    COL_END_USER,
    COL_LIMITED_TENDERING_REASON,
    COL_NOTICE_TYPE,
    COL_PUBLISHED,
    COL_REFERENCE,
    COL_SOLICITATION,
    COL_TITLE,
    COL_UNSPSC,
    COL_URL,
    is_defence_buyer,
    normalize_batch,
    normalize_row,
    parse_date,
    parse_unspsc,
)


def _row(**overrides):
    base = {
        COL_TITLE: "Multiple Victoria-Class Spares: HYDROPHONE, SONAR",
        COL_REFERENCE: "cb-215-13658629",
        COL_SOLICITATION: "W6399-27-TR21",
        COL_PUBLISHED: "2026-08-21",
        COL_CLOSING: "2026-08-28T23:59:00",
        COL_NOTICE_TYPE: "Request for Proposal",
        COL_UNSPSC: "*41115500",
        COL_END_USER: "Department of National Defence (DND)",
        COL_CONTRACTING_ENTITY: "Department of Public Works and Government Services",
        COL_URL: "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-215-13658629",
    }
    base.update(overrides)
    return base


# --- UNSPSC parsing -------------------------------------------------

def test_portal_url_built_for_the_two_verified_reference_shapes():
    # Both slugs below were read off the CanadaBuys portal's OWN
    # rendered listing links and matched character-for-character
    # against stored references — including for notices whose CSV row
    # carried no URL at all, which is exactly the gap this fills.
    assert canadabuys_portal_url("cb-957-33733663") == (
        "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-957-33733663"
    )
    assert canadabuys_portal_url("WS5801048823-Doc5822430726") == (
        "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/ws5801048823-doc5822430726"
    )


def test_portal_url_refuses_unverified_reference_shapes():
    # A constructed slug for this shape returned a real 404, so its
    # portal URL is evidently built some other way. An honest blank
    # beats a plausible-looking dead link.
    assert canadabuys_portal_url("PW-_KIN-519-8707") is None
    assert canadabuys_portal_url("PW-__QE-450-27248") is None
    assert canadabuys_portal_url("") is None
    assert canadabuys_portal_url(None) is None


def test_row_without_csv_url_falls_back_to_the_portal_url():
    result = normalize_row(_row(**{COL_URL: "", COL_REFERENCE: "cb-349-39395952"}))
    assert result["ui_link"] == (
        "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-349-39395952"
    )


def test_row_with_unverified_reference_shape_stays_blank():
    result = normalize_row(_row(**{COL_URL: "", COL_REFERENCE: "PW-_KIN-519-8707"}))
    assert result["ui_link"] is None


def test_csv_url_still_wins_when_present():
    # The feed's own URL points at MERX/Ariba where the tender is
    # actually hosted — that's a better destination than the portal
    # summary page, so it must not be overridden by the fallback.
    result = normalize_row(_row(**{
        COL_REFERENCE: "cb-349-39395952",
        COL_URL: "https://www.merx.com/some-real-notice",
    }))
    assert result["ui_link"] == "https://www.merx.com/some-real-notice"


def test_parse_unspsc_single_value():
    assert parse_unspsc("*10100000") == ["10100000"]


def test_parse_unspsc_multi_value_newline_separated():
    # 275 of 824 coded rows in a live pull were multi-valued — treating
    # this field as a scalar would discard most of the classification.
    assert parse_unspsc("*10191500\n*77121608") == ["10191500", "77121608"]


def test_parse_unspsc_handles_blank_and_non_numeric():
    assert parse_unspsc("") == []
    assert parse_unspsc(None) == []
    assert parse_unspsc("*not-a-code") == []


# --- dates ----------------------------------------------------------

def test_parse_date_handles_both_live_formats():
    assert parse_date("2026-08-28T23:59:00") == "2026-08-28T23:59:00"
    assert parse_date("2026-08-21") == "2026-08-21T00:00:00"


def test_parse_date_falls_back_to_raw():
    assert parse_date("sometime next spring") == "sometime next spring"
    assert parse_date(None) is None


# --- defence-buyer relevance ---------------------------------------

def test_defence_buyer_by_end_user_entity():
    assert is_defence_buyer(_row())


def test_defence_buyer_by_w_prefixed_solicitation_alone():
    # PSPC buys on behalf of DND, so the end-user field can name PSPC
    # while the W-prefix is what identifies it as a defence requirement.
    row = _row(**{
        COL_END_USER: "",
        COL_CONTRACTING_ENTITY: "Department of Public Works and Government Services",
        COL_SOLICITATION: "W0113-27CS25",
    })
    assert is_defence_buyer(row)


def test_non_defence_buyer_rejected():
    row = _row(**{
        COL_END_USER: "Parks Canada Agency (PC)",
        COL_CONTRACTING_ENTITY: "Parks Canada Agency (PC)",
        COL_SOLICITATION: "5P052-240474",
    })
    assert not is_defence_buyer(row)


def test_w_prefix_does_not_match_an_ordinary_word():
    # Anchored and digit-qualified so a solicitation like 'WEATHER-1'
    # is not mistaken for a DND reference.
    row = _row(**{
        COL_END_USER: "Environment and Climate Change Canada",
        COL_CONTRACTING_ENTITY: "Environment and Climate Change Canada",
        COL_SOLICITATION: "WEATHER-2026-01",
    })
    assert not is_defence_buyer(row)


# --- normalization --------------------------------------------------

def test_normalize_row_core_fields():
    r = normalize_row(_row())
    assert r["external_ref"] == "cb-215-13658629"
    assert r["name"] == "Multiple Victoria-Class Spares: HYDROPHONE, SONAR"
    assert r["country"] == "Canada"
    assert r["organization_name"] == "Department of National Defence (DND)"
    assert r["classification_code"] == "41115500"
    assert r["classification_scheme"] == "UNSPSC"
    assert r["response_deadline"] == "2026-08-28T23:59:00"


def test_normalize_row_keeps_every_code_not_just_the_first():
    r = normalize_row(_row(**{COL_UNSPSC: "*25172800\n*40161500"}))
    assert r["classification_code"] == "25172800"       # stored in naics_code
    assert r["all_classification_codes"] == ["25172800", "40161500"]


def test_normalize_row_without_codes_has_no_scheme():
    r = normalize_row(_row(**{COL_UNSPSC: ""}))
    assert r["classification_code"] is None
    assert r["classification_scheme"] is None


def test_normalize_row_has_no_restriction_when_field_absent_or_none():
    """
    Eligibility/Backup Phase 1 (2026-09) — live-verified against a
    real 905-row CanadaBuys pull: "None" is the real, literal value
    this field carries when a tender is NOT restricted (not merely
    an empty/missing field), and must not be surfaced as if it were
    a stated restriction.
    """
    r = normalize_row(_row())
    assert r["set_aside_code"] is None
    r2 = normalize_row(_row(**{COL_LIMITED_TENDERING_REASON: "None"}))
    assert r2["set_aside_code"] is None


def test_normalize_row_extracts_exclusive_rights_restriction():
    r = normalize_row(_row(**{COL_LIMITED_TENDERING_REASON: "Exclusive Rights"}))
    assert r["set_aside_code"] == "Exclusive Rights"
    assert "not open to all bidders" in r["set_aside_description"]


def test_normalize_row_extracts_no_response_restriction():
    r = normalize_row(_row(**{COL_LIMITED_TENDERING_REASON: "No response to bid solicitation"}))
    assert r["set_aside_code"] == "No response to bid solicitation"


def test_normalize_row_falls_back_to_solicitation_number():
    r = normalize_row(_row(**{COL_REFERENCE: ""}))
    assert r["external_ref"] == "W6399-27-TR21"


def test_normalize_row_raises_without_any_identifier():
    try:
        normalize_row(_row(**{COL_REFERENCE: "", COL_SOLICITATION: ""}))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_stage_mapping_from_notice_type():
    assert normalize_row(_row(**{COL_NOTICE_TYPE: "Request for Proposal"}))["stage"] == "rfp_issued"
    assert normalize_row(
        _row(**{COL_NOTICE_TYPE: "Advance Contract Award Notice"})
    )["stage"] == "contract_awarded"
    assert normalize_row(
        _row(**{COL_NOTICE_TYPE: "Request for Information (RFI)"})
    )["stage"] == "requirement_defined"


def test_unknown_notice_type_falls_back_safely():
    assert normalize_row(_row(**{COL_NOTICE_TYPE: "Something New"}))["stage"] == "requirement_defined"


# --- batch filtering ------------------------------------------------

def test_batch_keeps_real_defence_materiel():
    normalized, failures = normalize_batch([_row()])
    assert failures == []
    assert len(normalized) == 1
    assert normalized[0]["classification_code"] == "41115500"


def test_batch_drops_dnd_base_construction():
    # Real live title. DND runs large estates, so its feed carries
    # construction source lists that are not materiel — the same
    # problem India's MES data has.
    rows = [_row(**{COL_TITLE: "Open Construction Source List for CFB Halifax", COL_UNSPSC: ""})]
    normalized, failures = normalize_batch(rows)
    assert normalized == []
    assert failures == []


def test_batch_drops_non_defence_buyer():
    rows = [_row(**{
        COL_TITLE: "Parks Canada Uniform Program",
        COL_END_USER: "Parks Canada Agency (PC)",
        COL_CONTRACTING_ENTITY: "Parks Canada Agency (PC)",
        COL_SOLICITATION: "5P052-240474",
    })]
    normalized, _ = normalize_batch(rows)
    assert normalized == []


def test_batch_isolates_malformed_rows_as_failures():
    good = _row()
    bad = _row(**{COL_REFERENCE: "", COL_SOLICITATION: ""})
    normalized, failures = normalize_batch([good, bad])
    assert len(normalized) == 1
    assert len(failures) == 1


# --- award notices (a separate CSV — see canada_buys_ingestion.py) ---

def _award_row(**overrides):
    base = {
        COL_TITLE: "Damage Control Spares",
        COL_REFERENCE: "cb-121-94123499",
        COL_SOLICITATION: "W6399-27-TR21",
        "contractAwardDate-dateAttributionContrat": "2026-07-15",
        COL_UNSPSC: "*12160000",
        COL_END_USER: "Department of National Defence (DND)",
        COL_CONTRACTING_ENTITY: "Department of Public Works and Government Services",
        "awardStatus-attributionStatut-eng": "Active",
        "supplierLegalName-nomLegalFournisseur-eng": "GCPROC Ltd",
        "supplierAddressCountry-fournisseurAdressePays-eng": "Canada",
        "contactInfoName-informationsContactNom": "Jane Doe",
        "contactInfoEmail-informationsContactCourriel": "jane@pwgsc.gc.ca",
        "contactInfoPhone-contactInfoTelephone": "613-555-1234",
        "contactInfoAddressLine-contactInfoAdresseLigne-eng": "11 Laurier St",
        "contactInfoCity-contacterInfoVille-eng": "Gatineau",
        "contactInfoProvince-contacterInfoProvince-eng": "QC",
        "contactInfoPostalcode": "K1A0S5",
        "contactInfoCountry-contactInfoPays-eng": "Canada",
        "awardDescription-descriptionAttribution-eng": "",
    }
    base.update(overrides)
    return base


def test_award_row_core_fields_and_stage_is_always_awarded():
    r = normalize_award_row(_award_row())
    assert r["external_ref"] == "cb-121-94123499"
    assert r["stage"] == "contract_awarded"
    assert r["classification_code"] == "12160000"
    assert r["organization_name"] == "Department of National Defence (DND)"
    assert r["winner_name"] == "GCPROC Ltd"
    assert r["winner_country"] == "Canada"


def test_award_row_contact_address_is_built_from_the_richer_contact_block():
    """
    The award file publishes a full postal address for its contact —
    the open-tenders file (normalize_row) does not have this at all.
    """
    r = normalize_award_row(_award_row())
    assert r["contact_address"] == "11 Laurier St, Gatineau, QC, K1A0S5, Canada"


def test_award_row_reuses_the_verified_portal_url_fallback():
    # No notice-URL column exists in this file at all — the same
    # verified "cb-###-########" reconstruction already used for the
    # open-tenders file is reused, not duplicated.
    r = normalize_award_row(_award_row())
    assert r["ui_link"] == "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-121-94123499"


def test_award_row_falls_back_to_award_description_when_title_blank():
    r = normalize_award_row(_award_row(**{COL_TITLE: "", "awardDescription-descriptionAttribution-eng": "Spares kit"}))
    assert r["name"] == "Spares kit"


def test_award_row_no_winner_gives_none_not_empty_string():
    r = normalize_award_row(_award_row(**{"supplierLegalName-nomLegalFournisseur-eng": ""}))
    assert r["winner_name"] is None


def test_award_batch_skips_cancelled_awards():
    rows = [_award_row(**{"awardStatus-attributionStatut-eng": "Cancelled"})]
    normalized, failures = normalize_award_batch(rows)
    assert normalized == []
    assert failures == [], "a cancelled award is a skip, not a failure"


def test_award_batch_keeps_expired_awards():
    """
    'Expired' means the contract TERM ended, not that the award was
    invalid — real historical evidence a company won it, which is
    exactly what Competitor/OEM Intelligence needs.
    """
    rows = [_award_row(**{"awardStatus-attributionStatut-eng": "Expired"})]
    normalized, _ = normalize_award_batch(rows)
    assert len(normalized) == 1


def test_award_batch_drops_non_defence_buyer():
    rows = [_award_row(**{
        COL_END_USER: "Parks Canada Agency (PC)",
        COL_CONTRACTING_ENTITY: "Parks Canada Agency (PC)",
        COL_SOLICITATION: "5P052-240474",
    })]
    normalized, _ = normalize_award_batch(rows)
    assert normalized == []


def test_award_batch_isolates_malformed_rows_as_failures():
    good = _award_row()
    bad = _award_row(**{COL_REFERENCE: "", COL_SOLICITATION: ""})
    normalized, failures = normalize_award_batch([good, bad])
    assert len(normalized) == 1
    assert len(failures) == 1
