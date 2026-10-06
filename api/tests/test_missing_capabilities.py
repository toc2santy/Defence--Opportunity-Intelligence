"""
Tests for the three capabilities added by migration 053 (2026-10):
WEAPONS.AMMUNITION, TRAINING.SIMULATION, SPARES.REPLACEMENT.

The end-to-end tests seed their OWN tender (one per capability, using a code
the migration maps) instead of depending on whatever the ingestion happened
to store, so they pass on a fresh database too, and remove it afterwards.
"""

import uuid

import pytest

NEW = {
    "WEAPONS.AMMUNITION": {
        "code": "35331500",
        "title": "Supply of 5.56mm ammunition cartridges for infantry rifles",
        "product": "5.56mm ammunition, cartridges, rifles and small arms manufacturer",
    },
    "TRAINING.SIMULATION": {
        "code": "34152000",
        "title": "Delivery of tactical training simulators for armoured crews",
        "product": "military training simulator and live fire range equipment",
    },
    "SPARES.REPLACEMENT": {
        "code": "35421000",
        "title": "Mechanical spare parts for military vehicles",
        "product": "spare parts and replacement parts supplier for military vehicles",
    },
}


def _cap(db_cursor, code):
    db_cursor.execute("select id from capability_taxonomy where code = %s", (code,))
    row = db_cursor.fetchone()
    assert row is not None, f"{code} missing — apply db/migrations/053_missing_capabilities.sql"
    return row["id"]


def test_all_three_capabilities_exist_with_codes_and_keywords(db_cursor):
    for code in NEW:
        cap_id = _cap(db_cursor, code)
        db_cursor.execute("select count(*) as n from capability_taxonomy_keywords where capability_id = %s", (cap_id,))
        assert db_cursor.fetchone()["n"] >= 15, code

    db_cursor.execute("""
        select cpv_code from taxonomy_cpv_mapping m join capability_taxonomy ct on ct.id = m.capability_id
        where ct.code = 'WEAPONS.AMMUNITION'
    """)
    cpv = {r["cpv_code"] for r in db_cursor.fetchall()}
    assert {"35300000", "35330000", "35331500", "35320000"} <= cpv
    db_cursor.execute("""
        select naics_code from taxonomy_naics_mapping m join capability_taxonomy ct on ct.id = m.capability_id
        where ct.code = 'WEAPONS.AMMUNITION'
    """)
    assert {r["naics_code"] for r in db_cursor.fetchall()} == {"332992", "332993", "332994", "332995"}
    db_cursor.execute("""
        select cpv_code from taxonomy_cpv_mapping m join capability_taxonomy ct on ct.id = m.capability_id
        where ct.code = 'TRAINING.SIMULATION'
    """)
    assert {r["cpv_code"] for r in db_cursor.fetchall()} == {"34152000", "35740000", "35210000"}


def test_singular_and_plural_forms_both_exist(db_cursor):
    """scoring.py matches on word boundaries — 'rifle' does not match 'rifles' (the migration 034 bug)."""
    db_cursor.execute("""
        select ct.code, k.keyword from capability_taxonomy_keywords k
        join capability_taxonomy ct on ct.id = k.capability_id
        where ct.code in ('WEAPONS.AMMUNITION', 'TRAINING.SIMULATION', 'SPARES.REPLACEMENT')
    """)
    kws = {(r["code"], r["keyword"]) for r in db_cursor.fetchall()}
    for cap, one, many in [
        ("WEAPONS.AMMUNITION", "rifle", "rifles"), ("WEAPONS.AMMUNITION", "firearm", "firearms"),
        ("WEAPONS.AMMUNITION", "grenade", "grenades"), ("WEAPONS.AMMUNITION", "pistol", "pistols"),
        ("TRAINING.SIMULATION", "simulator", "simulators"), ("TRAINING.SIMULATION", "training aid", "training aids"),
        ("SPARES.REPLACEMENT", "spare part", "spare parts"), ("SPARES.REPLACEMENT", "replacement part", "replacement parts"),
    ]:
        assert (cap, one) in kws and (cap, many) in kws, (cap, one, many)


def test_non_english_keywords_actually_match_through_the_real_scorer():
    from app.scoring import score_text
    rows = [("cid", "WEAPONS.AMMUNITION", "w", "s", "střelivo", 3), ("cid", "WEAPONS.AMMUNITION", "w", "s", "боєприпаси", 3),
            ("cid2", "SPARES.REPLACEMENT", "s", "s", "náhradných dielov", 3)]
    assert score_text("NÁKUP STŘELIVA ČSS 2026 střelivo", rows)[0]["matched_keywords"] == ["střelivo"]
    assert score_text("Постачання БОЄПРИПАСИ для ЗСУ", rows)[0]["matched_keywords"] == ["боєприпаси"]
    assert score_text("Nákup náhradných dielov NON-IT", rows)[0]["code"] == "SPARES.REPLACEMENT"


@pytest.mark.parametrize("cap_code", list(NEW))
def test_product_classifies_confirms_and_matches_a_seeded_tender(client, auth_headers, db_cursor, cap_code):
    spec = NEW[cap_code]
    token = "".join(chr(97 + int(c, 16)) for c in uuid.uuid4().hex[:8])
    title = f"{spec['title']} lot {token}"
    db_cursor.execute(
        """insert into programmes (name, country, stage, naics_code, external_ref)
           values (%s, 'Testland', 'rfp_issued', %s, %s) returning id""",
        (title, spec["code"], f"test-fixture-newcap-{uuid.uuid4().hex}"),
    )
    programme_id = db_cursor.fetchone()["id"]
    product_id = None
    try:
        product_id = client.post("/products", headers=auth_headers,
                                 json={"name": f"{cap_code} product", "description": spec["product"], "trl": 9}).json()["id"]
        candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
        mine = next((c for c in candidates if c["code"] == cap_code), None)
        assert mine is not None, f"{cap_code} not suggested; got {[c['code'] for c in candidates]}"
        assert mine["confidence"] in ("medium", "high")

        r = client.post(f"/products/{product_id}/capabilities/{mine['capability_id']}/confirm", headers=auth_headers)
        assert r.status_code == 200, r.text
        matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]
        found = [m for m in matches if m["programme_name"] == title]
        assert found, f"seeded {cap_code} tender was not matched"
        assert found[0]["naics_match"] is True
    finally:
        db_cursor.execute("delete from opportunities where programme_id = %s", (programme_id,))
        db_cursor.execute("delete from programmes where id = %s", (programme_id,))
        if product_id:
            client.delete(f"/products/{product_id}", headers=auth_headers)
