"""
Unit tests for app.cppp_india_normalize — no network, no database.

The HTML fixture below is a trimmed copy of real markup captured from
eprocure.gov.in/cppp/latestactivetendersnew, not invented structure:
the cell order, the '<title> /<ref>/<tender id>' layout of the
Title/Ref.No./Tender Id cell, the '22-Aug-2026 01:10 PM' date format
and the real organisation strings all come from the live listing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cppp_india_normalize import (
    INDIA_DEFENCE_ORG_CODE,
    is_defence_organisation,
    is_facilities_works,
    is_uninformative_title,
    normalize_batch,
    normalize_row,
    parse_date,
    parse_rows,
)


REAL_PAGE_HTML = """
<table>
<tr>
  <th scope="col">Sl.No</th><th scope="col">e-Published Date</th>
  <th scope="col">Bid Submission Closing Date</th><th scope="col">Tender Opening Date</th>
  <th scope="col">Title/Ref.No./Tender Id</th><th scope="col">Organisation Name</th>
  <th scope="col">Corrigendum</th>
</tr>
<tr>
  <td>2.</td><td>22-Aug-2026 01:10 PM</td><td>01-Sep-2026 06:00 PM</td><td>03-Sep-2026 11:00 AM</td>
  <td><a href="https://eprocure.gov.in/cppp/tendersfullview/ABC123" title="External Url">Annual Repair and maintenance of Non-Residential buildings at Ftr HQ BSF Campus Kadamtal</a>/06/HQ-NBF/2026-27/2026_BSF_923357_1</td>
  <td>DG,BSF,MHA</td><td></td>
</tr>
<tr>
  <td>4.</td><td>22-Aug-2026 01:00 PM</td><td>12-Sep-2026 06:00 PM</td><td>14-Sep-2026 11:00 AM</td>
  <td><a href="https://eprocure.gov.in/cppp/tendersfullview/DEF456" title="External Url">AGE(I)(U)B/R-TOKEN-25/2026-27</a>/AGE(I)(U)B/R-TOKEN-25/2026-27/2026_MES_785853_1</td>
  <td>E-IN-C BRANCH - MILITARY ENGINEER SERVICES</td><td></td>
</tr>
<tr>
  <td>7.</td><td>22-Aug-2026 01:00 PM</td><td>07-Sep-2026 05:00 PM</td><td>09-Sep-2026 11:00 AM</td>
  <td><a href="https://eprocure.gov.in/cppp/tendersfullview/GHI789" title="External Url">Permanent Restoration of road from L030-SARANOO TO PEOMANYALAN</a>/SE/PMGSY/J/44/166831</td>
  <td>National Rural Roads Development Agency (NRRDA)</td><td></td>
</tr>
</table>
"""


def test_parse_rows_extracts_only_data_rows():
    rows = parse_rows(REAL_PAGE_HTML)
    assert len(rows) == 3  # header row excluded


def test_parse_rows_extracts_expected_fields():
    row = parse_rows(REAL_PAGE_HTML)[0]
    assert row["published"] == "22-Aug-2026 01:10 PM"
    assert row["closing"] == "01-Sep-2026 06:00 PM"
    assert row["organisation"] == "DG,BSF,MHA"
    assert row["detail_url"] == "https://eprocure.gov.in/cppp/tendersfullview/ABC123"


def test_normalize_row_takes_tender_id_as_final_slash_segment():
    rows = parse_rows(REAL_PAGE_HTML)
    assert normalize_row(rows[0])["external_ref"] == "2026_BSF_923357_1"


def test_normalize_row_handles_title_containing_slashes():
    # 'AGE(I)(U)B/R-TOKEN-25/2026-27' has slashes inside the title
    # itself — the tender id must still be the last segment, not a
    # fragment of the title.
    rows = parse_rows(REAL_PAGE_HTML)
    record = normalize_row(rows[1])
    assert record["external_ref"] == "2026_MES_785853_1"
    assert record["name"] == "AGE(I)(U)B/R-TOKEN-25/2026-27"


def test_normalize_row_sets_india_and_sentinel_code():
    record = normalize_row(parse_rows(REAL_PAGE_HTML)[0])
    assert record["country"] == "India"
    assert record["classification_code"] == INDIA_DEFENCE_ORG_CODE
    assert record["stage"] == "rfp_issued"


def test_normalize_row_converts_dates_to_iso():
    record = normalize_row(parse_rows(REAL_PAGE_HTML)[0])
    assert record["response_deadline"] == "2026-09-01T18:00:00"
    assert record["posted_date"] == "2026-08-22T13:10:00"


def test_parse_date_falls_back_to_raw_on_unexpected_format():
    assert parse_date("not a date") == "not a date"
    assert parse_date(None) is None


def test_normalize_row_raises_when_no_tender_id():
    try:
        normalize_row({"title_cell": "no slashes here", "organisation": "MES"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_batch_drops_all_three_real_rows_for_three_different_reasons():
    """
    All three rows in the live-captured fixture are dropped, each by a
    different filter — which is exactly what real CPPP data looks like:
      1. BSF "Annual Repair ... Non-Residential buildings" — defence
         organisation, but estate works.
      2. MES "AGE(I)(U)B/R-TOKEN-25/2026-27" — defence organisation,
         but a bare reference code describing nothing.
      3. NRRDA rural roads — not a defence organisation at all.
    None are failures: they parsed correctly, they just aren't
    procurement this platform covers.
    """
    normalized, failures = normalize_batch(parse_rows(REAL_PAGE_HTML))
    assert normalized == []
    assert failures == []


def test_batch_keeps_a_defence_org_row_with_a_real_materiel_title():
    # The positive case the fixture above deliberately lacks — proving
    # the filters exclude on content, not on the source itself.
    rows = parse_rows(REAL_PAGE_HTML.replace(
        "Annual Repair and maintenance of Non-Residential buildings at Ftr HQ BSF Campus Kadamtal",
        "Supply of radar surveillance systems",
    ))
    normalized, failures = normalize_batch(rows)
    assert failures == []
    assert [r["name"] for r in normalized] == ["Supply of radar surveillance systems"]


def test_defence_orgs_recognised():
    for org in [
        "E-IN-C BRANCH - MILITARY ENGINEER SERVICES",
        "DG,BSF,MHA",
        "Ministry of Defence",
        "DRDO",
        "Indian Navy",
        "Ordnance Factory Board",
        "Hindustan Aeronautics Limited (HAL)",
    ]:
        assert is_defence_organisation(org), org


def test_non_defence_orgs_rejected():
    for org in [
        "National Rural Roads Development Agency (NRRDA)",
        "Central Public Works Department (CPWD)",
        "Ministry of Power",
        None,
        "",
    ]:
        assert not is_defence_organisation(org), org


def test_acronyms_require_word_boundaries():
    # Substring matching these acronyms would wrongly flag ordinary
    # place and word names as defence organisations.
    assert not is_defence_organisation("BELGAUM Municipal Corporation")
    assert not is_defence_organisation("MESSAGE Systems Department")
    assert not is_defence_organisation("HALDIA Development Authority")


def test_ofb_successor_dpsus_are_recognised():
    # OFB was dissolved 1 Oct 2021 and its 41 units reorganised into
    # these seven companies — a real gap this list previously missed
    # entirely, since only the old "OFB" acronym was matched.
    for org in [
        "Yantra India Limited",
        "Munitions India Limited",
        "Armoured Vehicles Nigam Limited",
        "Advanced Weapons and Equipment India Limited",
        "Troop Comforts Limited",
        "India Optel Limited",
        "Gliders India Limited",
    ]:
        assert is_defence_organisation(org), org


def test_real_menial_mes_titles_are_excluded():
    # Every one of these is a real title observed in a live CPPP run —
    # they are what made the first ingest look like defence coverage
    # when it was actually building maintenance.
    for title in [
        "REPAIR/CLEANING OF EXISTING SEWAGE LINE, SOIL WASTE PIPES",
        "SPECIAL REPAIR TO ROOF AND SUNKEN TREATMENT OF TOILETS IN BLDG NO P-345",
        "PROVN OF SHED AT BLOOMING BUDS PLAY SCHOOL STORE",
        "CERTAIN REPAIRS TO COMPOUND WALL, GATE / GRILL GRATING AND CHAIN LINK FENCING",
        "REPAIR REPLACEMENT OF CONVENTIONAL LIGHT FITTINGS WITH ENERGY EFFICIENT LED",
        "LAYING OF SIX-A-SIDE HOCKEY GROUND WITH ALLIED ACCESSORIES",
    ]:
        assert is_facilities_works(title), title


def test_real_defence_materiel_is_kept_even_when_worded_as_repair():
    # 'Repair and maintenance services of warships' is its own CPV
    # code (5064) in the UK/EU sources — a blanket "repair" exclusion
    # would wrongly discard genuine capability procurement.
    for title in [
        "Repair and maintenance of warships",
        "Overhaul of aero engine assemblies",
        "Supply of radar surveillance systems",
        "Procurement of ammunition and propellant",
        "Refit of submarine sonar array",
    ]:
        assert not is_facilities_works(title), title


def test_materiel_terms_do_not_rescue_estate_works_via_substring():
    # Regression test for real leakage found against live data: a bare
    # substring match let "TANK" rescue septic/water tanks and "ENGINE"
    # rescue "Detailed Engineering", so estate works sailed through.
    for title in [
        "REPAIRS/ REPLACEMENT OF SEWAGE LINES, MANHOLES, SEPTIC TANKS/ SOAK WELLS",
        "ADDN / ALTN OF RCC WATER TANK TO POLYETHYLENE WATER STORAGE TANK IN BLDG P-167",
        "Consultancy Services for Detailed Engineering including Design Drawings",
        "MAINT REPAIR TO OH RESERVIOR SUMPS SWTs FIRE FIGHTING TANKS",
    ]:
        assert is_facilities_works(title), title


def test_genuine_midhani_materiel_survives_all_filters():
    # The only two records out of 66 live CPPP tenders that were real
    # defence materiel — both from MIDHANI, the defence metals PSU.
    for title in ["COBALT METAL UNWROUGHT", "PURE IRON"]:
        assert not is_facilities_works(title), title


def test_reference_code_only_titles_are_dropped():
    # Real MES titles that are nothing but an internal token — they
    # describe no subject, so they can never match a capability.
    for title in [
        "AGE(I)(U)B/R-TOKEN-25/2026-27",
        "AGE(I)(U)B/R-TOKEN-30/2026-27",
        "25/NIT/EE/AAD/2026-27",
    ]:
        assert is_uninformative_title(title), title


def test_descriptive_titles_are_not_treated_as_uninformative():
    for title in ["Supply of radar surveillance systems", "COBALT METAL UNWROUGHT"]:
        assert not is_uninformative_title(title), title


def test_facilities_filter_handles_missing_title():
    assert is_facilities_works(None) is False
    assert is_facilities_works("") is False


def test_batch_drops_menial_work_from_a_defence_organisation():
    # Published by MES (a defence org, so it passes the org filter)
    # but estate work — must still be dropped.
    rows = [{
        "title_cell": "SPECIAL REPAIR TO ROOF AND SUNKEN TREATMENT OF TOILETS/REF/2026_MES_1_1",
        "title_link_text": "SPECIAL REPAIR TO ROOF AND SUNKEN TREATMENT OF TOILETS",
        "organisation": "E-IN-C BRANCH - MILITARY ENGINEER SERVICES",
        "published": "22-Aug-2026 01:00 PM", "closing": "10-Sep-2026 06:00 PM",
        "opening": "", "detail_url": None,
    }]
    normalized, failures = normalize_batch(rows)
    assert normalized == []
    assert failures == []


def test_batch_keeps_real_materiel_from_a_defence_organisation():
    rows = [{
        "title_cell": "Supply of radar surveillance systems/REF/2026_DRDO_9_1",
        "title_link_text": "Supply of radar surveillance systems",
        "organisation": "DRDO",
        "published": "22-Aug-2026 01:00 PM", "closing": "10-Sep-2026 06:00 PM",
        "opening": "", "detail_url": None,
    }]
    normalized, _ = normalize_batch(rows)
    assert len(normalized) == 1
    assert normalized[0]["external_ref"] == "2026_DRDO_9_1"


def test_parse_rows_returns_empty_on_layout_change():
    # A changed layout must surface as zero rows, never as wrong data.
    assert parse_rows("<html><body><p>no table here</p></body></html>") == []
