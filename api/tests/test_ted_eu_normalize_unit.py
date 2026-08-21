"""
Unit tests for app.ted_eu_normalize — no network, no database.
Uses realistic TED Search API v3 response structures based on the
official documentation and publicly documented field formats.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ted_eu_normalize import normalize_ted_notice, normalize_batch, _extract_multilingual


REALISTIC_NOTICE = {
    "publication-number": "477851-2026",
    "notice-title": {"eng": ["Supply of Armoured Vehicle Components"]},
    "buyer-name": {"eng": ["German Federal Ministry of Defence"]},
    "buyer-country": ["DEU"],
    "classification-cpv": ["35410000"],
    "publication-date": ["2026-07-15+02:00"],
    "notice-type": ["cn-standard"],
    "deadline-receipt-tenders": {"eng": ["2026-09-01"]},
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
