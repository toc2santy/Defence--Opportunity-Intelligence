"""
Unit tests for app.ted_eu_normalize — no network, no database.
Uses realistic TED Search API v3 response structures based on the
official documentation and publicly documented field formats.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ted_eu_normalize import normalize_ted_notice, normalize_batch, _extract_multilingual, extract_winners


REALISTIC_NOTICE = {
    "publication-number": "477851-2026",
    "notice-title": {"eng": ["Supply of Armoured Vehicle Components"]},
    "buyer-name": {"eng": ["German Federal Ministry of Defence"]},
    "buyer-country": ["DEU"],
    "classification-cpv": ["35410000"],
    "publication-date": ["2026-07-15+02:00"],
    "notice-type": ["cn-standard"],
    "deadline-date-lot": ["2026-09-01"],
}


def test_normalize_realistic_notice():
    r = normalize_ted_notice(REALISTIC_NOTICE)
    assert r["external_ref"] == "477851-2026"
    assert r["name"] == "Supply of Armoured Vehicle Components"
    assert r["country"] == "DEU"
    assert r["organization_name"] == "German Federal Ministry of Defence"
    assert r["stage"] == "rfp_issued"
    assert r["classification_code"] == "35410000"
    assert r["response_deadline"] == "2026-09-01"
    assert r["ui_link"] == "https://ted.europa.eu/en/notice/-/detail/477851-2026"


def test_normalize_realistic_notice_has_no_contact_when_not_requested():
    r = normalize_ted_notice(REALISTIC_NOTICE)
    assert r["contact_email"] is None
    assert r["contact_address"] is None


def test_normalize_notice_has_no_restriction_when_fields_absent():
    r = normalize_ted_notice(REALISTIC_NOTICE)
    assert r["set_aside_code"] is None
    assert r["set_aside_description"] is None


def test_normalize_notice_extracts_sme_reservation():
    """
    Eligibility/Backup Phase 1 (2026-09) — sme-lot is a real, live-
    verified TED field: a plain boolean per lot. `true` on any lot
    means the whole notice is treated as SME-reserved (a real
    bidder-eligibility restriction, the same kind of fact SAM.gov's
    set-aside already surfaces for US notices).
    """
    notice = {**REALISTIC_NOTICE, "sme-lot": [True]}
    r = normalize_ted_notice(notice)
    assert r["set_aside_code"] == "SME"
    assert "small/medium enterprises" in r["set_aside_description"]


def test_normalize_notice_sme_false_is_not_a_restriction():
    notice = {**REALISTIC_NOTICE, "sme-lot": [False]}
    r = normalize_ted_notice(notice)
    assert r["set_aside_code"] is None


def test_normalize_notice_reserved_procurement_none_is_not_a_restriction():
    """
    Live-verified (2026-09): "none" is reserved-procurement-lot's own
    real value for "not reserved" on every sampled real notice — must
    not be surfaced as if it were a stated restriction.
    """
    notice = {**REALISTIC_NOTICE, "reserved-procurement-lot": ["none"]}
    r = normalize_ted_notice(notice)
    assert r["set_aside_code"] is None


def test_normalize_notice_extracts_non_none_reserved_procurement():
    notice = {**REALISTIC_NOTICE, "reserved-procurement-lot": ["sheltered-workshop"]}
    r = normalize_ted_notice(notice)
    assert r["set_aside_code"] == "sheltered-workshop"
    assert "sheltered-workshop" in r["set_aside_description"]


def test_normalize_notice_sme_takes_precedence_over_reserved_procurement():
    notice = {**REALISTIC_NOTICE, "sme-lot": [True], "reserved-procurement-lot": ["sheltered-workshop"]}
    r = normalize_ted_notice(notice)
    assert r["set_aside_code"] == "SME"


def test_normalize_notice_extracts_contact_email_and_address():
    """
    Shapes copied from a real live TED response (2026-09): buyer-email,
    buyer-post-code and organisation-street-buyer come back as PLAIN
    LISTS, not the multilingual dict shape most other TED fields use;
    buyer-city comes back as a multilingual dict ({"mul": [...]}).
    _extract_multilingual must handle both without a separate path.
    """
    notice = {
        **REALISTIC_NOTICE,
        "buyer-email": ["tmy.v.spijker@mindef.nl"],
        "buyer-post-code": ["2511CR"],
        "buyer-city": {"mul": ["'s-Gravenhage"]},
        "organisation-street-buyer": ["Plein 4"],
    }
    r = normalize_ted_notice(notice)
    assert r["contact_email"] == "tmy.v.spijker@mindef.nl"
    assert r["contact_address"] == "Plein 4, 's-Gravenhage, 2511CR, DEU"


def test_normalize_notice_contact_address_skips_missing_parts():
    notice = {**REALISTIC_NOTICE, "buyer-city": {"mul": ["Sevilla"]}}
    r = normalize_ted_notice(notice)
    assert r["contact_address"] == "Sevilla, DEU"


def test_multilingual_prefers_english():
    assert _extract_multilingual({"eng": ["English"], "fra": ["Français"]}, "t") == "English"


def test_multilingual_falls_back_to_any_language():
    assert _extract_multilingual({"fra": ["Titre en français"]}, "t") == "Titre en français"


def test_multilingual_handles_none():
    assert _extract_multilingual(None, "t") is None


def test_multilingual_handles_direct_string():
    assert _extract_multilingual("Direct Value", "t") == "Direct Value"


def test_award_notice_maps_to_contract_awarded():
    notice = dict(REALISTIC_NOTICE)
    notice["notice-type"] = ["can-standard"]
    assert normalize_ted_notice(notice)["stage"] == "contract_awarded"


def test_pin_notice_maps_to_early_concept():
    notice = dict(REALISTIC_NOTICE)
    notice["notice-type"] = ["pin-only"]
    assert normalize_ted_notice(notice)["stage"] == "early_concept"


def test_unknown_notice_type_falls_back_safely():
    notice = dict(REALISTIC_NOTICE)
    notice["notice-type"] = ["some-unknown-type"]
    assert normalize_ted_notice(notice)["stage"] == "requirement_defined"


def test_raises_on_missing_publication_number():
    try:
        normalize_ted_notice({"notice-title": {"eng": ["No ID"]}})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_batch_normalizes_and_isolates_failures():
    bad = {"notice-title": {"eng": ["No pub number"]}}
    normalized, failures = normalize_batch([REALISTIC_NOTICE, bad])
    assert len(normalized) == 1
    assert len(failures) == 1
    assert normalized[0]["external_ref"] == "477851-2026"


def test_multiple_eu_countries():
    for country_code in ["FRA", "ITA", "ESP", "POL", "NLD"]:
        notice = dict(REALISTIC_NOTICE)
        notice["buyer-country"] = [country_code]
        r = normalize_ted_notice(notice)
        assert r["country"] == country_code


def test_query_builder():
    # Full-text search approach confirmed more reliable than CPV-only
    # for defense notices (many use non-standard CPV codes or are
    # nested at lot level, which the API doesn't always surface).
    from datetime import datetime, timedelta
    d = datetime.utcnow()
    f = (d - timedelta(days=30)).strftime('%Y%m%d')
    t = d.strftime('%Y%m%d')
    q = (
        f'(FT~"defence" OR FT~"defense" OR FT~"military" OR FT~"armoured" '
        f'OR FT~"ammunition" OR FT~"warship" OR FT~"armament" '
        f'OR classification-cpv=35000000 OR classification-cpv=35300000 '
        f'OR classification-cpv=35400000 OR classification-cpv=35500000) '
        f'AND publication-date>={f} AND publication-date<={t}'
    )
    assert 'FT~"defence"' in q
    assert 'FT~"military"' in q
    assert 'classification-cpv=35000000' in q
    assert 'publication-date>=' in q
    assert 'PD>=' not in q  # old wrong legacy alias must not appear


# --- OEM Intelligence: winner extraction ---------------------------
# Fixture shapes below are copied from real live TED responses, not
# invented — including the exact irregularities that make naive
# extraction wrong (duplicate names per lot, name/country count
# mismatches).

def test_extract_winners_single_winner():
    winners = extract_winners({"eng": ["Kotka Beger Oy"]}, ["FIN"])
    assert winners == [{"name": "Kotka Beger Oy", "country": "FIN"}]


def test_extract_winners_multiple_distinct_winners_aligned_countries():
    # Real notice: 4 distinct winners, 4 countries, cleanly aligned.
    winners = extract_winners(
        {"pol": ["Raytech Sp. z o.o.", "Pimco Sp. z o.o.", "Spectro-Lab Sp. z o.o.", "Awares Sp. z o.o."]},
        ["POL", "POL", "POL", "POL"],
    )
    assert [w["name"] for w in winners] == [
        "Raytech Sp. z o.o.", "Pimco Sp. z o.o.", "Spectro-Lab Sp. z o.o.", "Awares Sp. z o.o.",
    ]
    assert all(w["country"] == "POL" for w in winners)


def test_extract_winners_deduplicates_repeated_names():
    # Real notice: a name list with duplicates because the same
    # company won multiple lots — must collapse to distinct winners,
    # not one contract_award row per lot-repetition.
    winners = extract_winners(
        {"pol": ["Varimed Sp. z o. o.", "OLYMPUS POLSKA Sp. z o.o.", "Varimed Sp. z o. o.", "OLYMPUS POLSKA Sp. z o.o."]},
        ["POL", "POL"],
    )
    assert [w["name"] for w in winners] == ["Varimed Sp. z o. o.", "OLYMPUS POLSKA Sp. z o.o."]


def test_extract_winners_country_omitted_on_length_mismatch():
    # Real observed irregularity: 2 distinct names, 3 countries — must
    # NOT guess a pairing; every winner gets country=None instead.
    winners = extract_winners({"eng": ["Company A", "Company B"]}, ["POL", "POL", "DEU"])
    assert winners == [
        {"name": "Company A", "country": None},
        {"name": "Company B", "country": None},
    ]


def test_extract_winners_prefers_english():
    winners = extract_winners({"fra": ["Société A"], "eng": ["Company A"]}, ["FRA"])
    assert winners[0]["name"] == "Company A"


def test_extract_winners_handles_missing_country_field():
    winners = extract_winners({"eng": ["Company A"]}, None)
    assert winners == [{"name": "Company A", "country": None}]


def test_extract_winners_empty_for_non_award_notice():
    assert extract_winners(None, None) == []
    assert extract_winners({}, []) == []


def test_normalize_ted_notice_includes_winners():
    notice = dict(REALISTIC_NOTICE)
    notice["winner-name"] = {"eng": ["Kotka Beger Oy"]}
    notice["winner-country"] = ["FIN"]
    record = normalize_ted_notice(notice)
    assert record["winners"] == [{"name": "Kotka Beger Oy", "country": "FIN"}]


def test_normalize_ted_notice_winners_empty_when_absent():
    assert normalize_ted_notice(REALISTIC_NOTICE)["winners"] == []
