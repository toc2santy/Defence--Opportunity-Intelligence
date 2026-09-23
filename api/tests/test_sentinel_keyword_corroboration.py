"""
Integration tests for the organisation-sentinel keyword-corroboration
fix (2026-09) — see programme_matching.py's own comment for the real,
user-reported false positive this closes: a CPPP (IN-DEF sentinel)
tender about street-light repair scored 92%/high for a UAV product,
because its title contains "...UAV KUMBHIGRAM..." (a real Indian
military station name in Assam), not a reference to unmanned aerial
vehicles — the bare 3-letter keyword "uav" still matched it at a
genuine word boundary, and a sentinel row has zero real classification
evidence to begin with, so that one short-token match was the ONLY
thing standing between "genuinely relevant" and "coincidental
collision".
"""

import uuid


def _seed_cppp_product(client, auth_headers, db_cursor, programme_name):
    db_cursor.execute("select id from sources where name = 'CPPP (Central Public Procurement Portal, India)'")
    source_id = db_cursor.fetchone()["id"]
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (programme_name, "India", "rfp_issued", source_id, "IN-DEF", f"test-sentinel-{uuid.uuid4().hex}"),
    )
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Sentinel Test UAV", "description": "UAV integration, unmanned aerial systems", "trl": 7},
    ).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    uav = next(c for c in candidates if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav['capability_id']}/confirm", headers=auth_headers)
    matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]
    return matches


def test_bare_short_acronym_is_not_enough_corroboration_for_a_sentinel_row(client, auth_headers, db_cursor):
    """
    The exact real false positive, reproduced: a sentinel-sourced
    tender whose only keyword hit is the bare "uav" token, sitting
    inside an unrelated place name, must NOT be matched at all — not
    even at low confidence, since a sentinel row has no other
    evidence to fall back on.
    """
    name = f"SPECIAL REPAIR OF STREET LIGHT AT VARIOUS LOCATIONS INCL UAV KUMBHIGRAM {uuid.uuid4().hex[:8]}"
    matches = _seed_cppp_product(client, auth_headers, db_cursor, name)
    assert not any(m["programme_name"] == name for m in matches), \
        "a bare 3-letter keyword match must not be sufficient corroboration for an organisation-sentinel row"


def test_a_real_multi_word_phrase_still_corroborates_a_sentinel_row(client, auth_headers, db_cursor):
    """
    The fix must not be a blanket ban on short keywords being useful —
    a genuine multi-word phrase match ("unmanned aerial") is real
    evidence and must still let a sentinel row through, same as
    before this fix.
    """
    name = f"PROCUREMENT OF UNMANNED AERIAL SYSTEM FOR SURVEILLANCE {uuid.uuid4().hex[:8]}"
    matches = _seed_cppp_product(client, auth_headers, db_cursor, name)
    assert any(m["programme_name"] == name for m in matches), \
        "a genuine multi-word keyword phrase must still corroborate a sentinel-only match"


def test_a_longer_single_word_still_corroborates_a_sentinel_row(client, auth_headers, db_cursor):
    """A 5+ character single-word keyword ("drone") is real evidence too, not just phrases."""
    name = f"PROCUREMENT OF DRONE SYSTEMS FOR AERIAL SURVEILLANCE {uuid.uuid4().hex[:8]}"
    matches = _seed_cppp_product(client, auth_headers, db_cursor, name)
    assert any(m["programme_name"] == name for m in matches), \
        "a real 5+ character keyword must still corroborate a sentinel-only match"
