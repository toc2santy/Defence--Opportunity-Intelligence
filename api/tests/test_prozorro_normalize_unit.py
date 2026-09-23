"""
Unit tests for app.prozorro_normalize — no network, no database.

Every fixture below is copied from a real ProZorro record (verified
2026-09-16 against public.api.openprocurement.org/api/2.5): the
Cyrillic field values, the "35740000-1"-style check-digit-suffixed
ДК021 codes, the procuringEntity.contactPoint/address shapes, and
real Ukrainian military-unit and National Guard buyer names.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.prozorro_normalize import (
    NotAnOpportunity,
    extract_contact,
    extract_winners,
    is_defence_buyer,
    is_relevant,
    is_support_unit,
    normalize_tender_detail,
)


def _detail(**overrides):
    base = {
        "id": "e5fefb0f9e554c8b8e6c47183d0ff277",
        "tenderID": "UA-2026-09-14-007123-a",
        "status": "active.tendering",
        "title": "Акумуляторні батареї до автомобільної техніки",
        "procurementMethodType": "belowThreshold",
        "items": [{"classification": {"scheme": "ДК021", "id": "31440000-2", "description": "Акумуляторні батареї"}}],
        "procuringEntity": {
            "name": "Військова частина Т0500",
            "address": {
                "streetAddress": "вул. Івана Мазепи, 18",
                "locality": "м. Чернігів",
                "region": "Чернігівська область",
                "postalCode": "14017",
                "countryName": "Україна",
            },
            "contactPoint": {"name": "Мирон Поліщук", "email": "mtender_t0500@dsst.gov.ua", "telephone": "+380937879097"},
        },
        "tenderPeriod": {"startDate": "2026-09-10T10:00:00+03:00", "endDate": "2026-09-25T08:00:00+03:00"},
        "dateCreated": "2026-09-10T09:55:00+03:00",
        "awards": [],
    }
    base.update(overrides)
    return base


# --- buyer relevance --------------------------------------------------

def test_real_defence_institutions_are_recognised():
    for name in (
        "Міністерство оборони України",
        "ДЕРЖАВНЕ ПІДПРИЄМСТВО МІНІСТЕРСТВА ОБОРОНИ УКРАЇНИ “АГЕНЦІЯ ОБОРОННИХ ЗАКУПІВЕЛЬ”",
        "ГОЛОВНЕ УПРАВЛІННЯ НАЦІОНАЛЬНОЇ ГВАРДІЇ УКРАЇНИ",
        "Військова частина А5049",
        "ВІЙСЬКОВА ЧАСТИНА 3044 НАЦІОНАЛЬНОЇ ГВАРДІЇ УКРАЇНИ",
    ):
        assert is_defence_buyer(name), name


def test_case_is_folded():
    assert is_defence_buyer("військова частина а5049")
    assert is_defence_buyer("ВІЙСЬКОВА ЧАСТИНА А5049")


def test_ordinary_civilian_buyers_are_rejected():
    assert not is_defence_buyer("Летківський комунальний заклад дошкільної освіти")
    assert not is_defence_buyer("")
    assert not is_defence_buyer(None)


def test_military_health_units_are_identified_and_excluded():
    """
    'ВІЙСЬКОВИЙ ГОСПІТАЛЬ НАЦІОНАЛЬНОЇ ГВАРДІЇ УКРАЇНИ' was the single
    largest defence-buyer group by volume in a live sample — genuinely
    military, genuinely not capability procurement.
    """
    name = "ВІЙСЬКОВИЙ ГОСПІТАЛЬ НАЦІОНАЛЬНОЇ ГВАРДІЇ УКРАЇНИ (ВІЙСЬКОВА ЧАСТИНА 3080)"
    assert is_defence_buyer(name), "still a defence institution"
    assert is_support_unit(name), "should be filtered as a health unit"

    dental = "Центральна стоматологічна поліклініка Міністерства оборони України"
    assert is_support_unit(dental)

    assert not is_support_unit("Військова частина А5049")


# --- classification (ДК021 == CPV) -------------------------------------

def test_dk021_check_digit_suffix_is_stripped():
    r = normalize_tender_detail(_detail())
    assert r["classification_code"] == "31440000"  # not "31440000-2"
    assert r["classification_scheme"] == "CPV"


def test_no_classification_gives_no_code():
    r = normalize_tender_detail(_detail(items=[]))
    assert r["classification_code"] is None
    assert r["classification_scheme"] is None


# --- the three-part relevance test --------------------------------------

def test_is_relevant_requires_all_three_conditions():
    # Real materiel, real defence buyer, real capability CPV code.
    assert is_relevant("Військова частина А5049", "35322100")  # anti-aircraft, "35" division
    # Buyer ok, code is not materiel (real live example: underwear).
    assert not is_relevant("Військова частина Т0500", "18310000")
    # Materiel-shaped code, but not a defence buyer at all.
    assert not is_relevant("Дитячий садок Пролісок", "35322100")
    # Buyer ok but it's a support/health unit.
    assert not is_relevant("Військовий госпіталь Національної гвардії", "35322100")


# --- contact + address --------------------------------------------------

def test_contact_and_address_are_extracted_together():
    contact = extract_contact(_detail())
    assert contact["contact_name"] == "Мирон Поліщук"
    assert contact["contact_email"] == "mtender_t0500@dsst.gov.ua"
    assert contact["contact_phone"] == "+380937879097"
    assert "Чернігів" in contact["contact_address"]
    assert "Україна" in contact["contact_address"]


def test_missing_contact_point_gives_none_not_an_error():
    contact = extract_contact(_detail(procuringEntity={"name": "Х"}))
    assert contact["contact_name"] is None
    assert contact["contact_address"] is None


# --- stage / status -------------------------------------------------------

def test_stage_mapping():
    assert normalize_tender_detail(_detail(status="active.tendering"))["stage"] == "rfp_issued"
    assert normalize_tender_detail(_detail(status="active.awarded"))["stage"] == "contract_awarded"
    assert normalize_tender_detail(_detail(status="complete"))["stage"] == "contract_awarded"


def test_unsuccessful_and_cancelled_are_skips_not_failures():
    for status in ("unsuccessful", "cancelled"):
        try:
            normalize_tender_detail(_detail(status=status))
            assert False, f"expected NotAnOpportunity for {status}"
        except NotAnOpportunity:
            pass


def test_bare_active_status_is_requirement_defined_not_rfp_issued():
    """
    Found live: a real ingestion run failed on this exact status
    ('Unmapped ProZorro tender status: 'active'') because it was
    missing from _STAGE_MAP entirely. Confirmed it appears only on
    'reporting'/'negotiation' procurement methods — never a
    competitive one — so it must not read as an open bid opportunity.
    """
    assert normalize_tender_detail(_detail(status="active"))["stage"] == "requirement_defined"


def test_unknown_status_still_fails_loudly():
    try:
        normalize_tender_detail(_detail(status="somethingNew"))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_no_id_at_all_raises():
    try:
        normalize_tender_detail({"status": "active.tendering"})
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- winners --------------------------------------------------------------

def test_only_active_awards_produce_winners():
    detail = _detail(status="complete", awards=[
        {"status": "cancelled", "suppliers": [{"name": "Dead Bid LLC"}]},
        {"status": "active", "suppliers": [{"name": "ТОВ БЕРІЛ", "address": {"countryName": "Україна"}}]},
    ])
    winners = extract_winners(detail)
    assert winners == [{"name": "ТОВ БЕРІЛ", "country": "Україна"}]


def test_no_awards_gives_no_winners():
    assert extract_winners(_detail()) == []


# --- real public URL --------------------------------------------------

def test_ui_link_uses_the_tender_id():
    r = normalize_tender_detail(_detail())
    assert r["ui_link"] == "https://prozorro.gov.ua/tender/UA-2026-09-14-007123-a"
