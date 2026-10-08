"""
Keyword-only matching (2026-10, migration 054) through the real API.

The test stack runs with KEYWORD_ONLY_MATCHING=true (docker-compose.test.yml).
Every tender is seeded here under a code NO capability maps, so a match can
only come from a title keyword; everything seeded is removed afterwards.

The flag-OFF and per-call-override behaviour is tested separately, in
process, in test_keyword_only_unit.py.
"""

import uuid

import pytest

UNMAPPED_CODE = "99999999"   # not in any mapping table, in any scheme


def _token():
    return "".join(chr(97 + int(c, 16)) for c in uuid.uuid4().hex[:8])


@pytest.fixture
def tenders(db_cursor):
    t = _token()
    specs = {
        "spares": f"Watercraft Spare Parts lot {t}",
        "weapons": f"Adquisicion de municiones para entrenamiento {t}",        # accent-less spelling of the flagged 'municiones'
        "training": f"Purchase of a flight simulator for crews {t}",   # training: no standalone keywords
        "generic_only": f"Naval surveillance of the harbour lawn {t}",     # generic words only
        "english_ammunition_plant": f"Army Ammunition Plant storage tank works {t}",   # deliberately NOT flagged
        "bare_simulator": f"Ultrasound Simulator for clinic {t}",           # deliberately NOT flagged
        "spares_inflected_only": f"Sparely populated region survey {t}",   # must not match 'spares'
    }
    ids = {}
    for name, title in specs.items():
        db_cursor.execute(
            """insert into programmes (name, country, stage, naics_code, external_ref)
               values (%s, 'Testland', 'rfp_issued', %s, %s) returning id""",
            (title, UNMAPPED_CODE, f"test-fixture-kwonly-{uuid.uuid4().hex}"),
        )
        ids[name] = (db_cursor.fetchone()["id"], title)
    yield ids
    all_ids = [i for i, _ in ids.values()]
    db_cursor.execute("delete from opportunities where programme_id = any(%s::uuid[])", (all_ids,))
    db_cursor.execute("delete from programmes where id = any(%s::uuid[])", (all_ids,))


def _confirmed_product(client, auth_headers, wanted_codes, description):
    product_id = client.post("/products", headers=auth_headers,
                             json={"name": "kw-only product", "description": description, "trl": 9}).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    for code in wanted_codes:
        cand = next((c for c in candidates if c["code"] == code), None)
        assert cand is not None, f"{code} not suggested; got {[c['code'] for c in candidates]}"
        r = client.post(f"/products/{product_id}/capabilities/{cand['capability_id']}/confirm", headers=auth_headers)
        assert r.status_code == 200, r.text
    return product_id


def test_standalone_keyword_makes_an_unmapped_code_tender_a_capped_keyword_match(client, auth_headers, tenders):
    product_id = _confirmed_product(
        client, auth_headers,
        ["SPARES.REPLACEMENT", "WEAPONS.AMMUNITION", "TRAINING.SIMULATION"],
        "spare parts and replacement parts supplier; ammunition, small arms and firearms manufacturer; "
        "military training simulator and live fire range equipment",
    )
    try:
        resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()
        by_title = {m["programme_name"]: m for m in resp["matches"]}

        for name in ("spares", "weapons"):
            title = tenders[name][1]
            assert title in by_title, f"{name} tender (unmapped code, standalone keyword) was not matched"
            m = by_title[title]
            assert m["match_basis"] == "keyword"
            assert m["naics_match"] is False and m["matched_classification_code"] is None
            assert m["confidence"] in ("low", "medium"), "keyword-only must never be high"

        assert tenders["training"][1] not in by_title, (
            "TRAINING.SIMULATION has no standalone keyword (0 of 4 reviewed keyword-only hits were right)")
        assert tenders["generic_only"][1] not in by_title, "generic words alone must not create a candidate"
        assert tenders["english_ammunition_plant"][1] not in by_title, "English 'ammunition' is not standalone (plants/services)"
        assert tenders["bare_simulator"][1] not in by_title, "a bare 'simulator' is not standalone (medical/test simulators)"
        assert tenders["spares_inflected_only"][1] not in by_title, "'sparely' is not the word 'spares'"
        assert resp["trace"]["keyword_only_enabled"] is True
        assert resp["trace"]["keyword_only_matches"] >= 2

        # persisted + listed with its basis, so the UI can tag it
        opps = client.get("/opportunities", headers=auth_headers).json()
        mine = {o["programme_name"]: o for o in opps if o["product_id"] == product_id}
        assert mine[tenders["spares"][1]]["match_basis"] == "keyword"
    finally:
        client.delete(f"/products/{product_id}", headers=auth_headers)


def test_keyword_for_a_capability_the_product_did_not_confirm_is_not_used(client, auth_headers, tenders):
    product_id = _confirmed_product(client, auth_headers, ["SPARES.REPLACEMENT"], "spare parts and replacement parts supplier")
    try:
        titles = {m["programme_name"] for m in
                  client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]}
        assert tenders["spares"][1] in titles
        assert tenders["weapons"][1] not in titles, "ammunition tender must not match a spares-only product"
        assert tenders["training"][1] not in titles
    finally:
        client.delete(f"/products/{product_id}", headers=auth_headers)


def test_platform_admin_can_flag_and_unflag_a_keyword_but_others_cannot(client, auth_headers, db_cursor):
    import base64, json
    payload = auth_headers["Authorization"].split(" ")[1].split(".")[1]
    uid = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]

    db_cursor.execute("select k.id, k.standalone from capability_taxonomy_keywords k join capability_taxonomy ct on ct.id = k.capability_id "
                      "where ct.code = 'SPARES.REPLACEMENT' and k.keyword = 'spares'")
    kw = db_cursor.fetchone()
    assert kw is not None and kw["standalone"] is True

    assert client.patch(f"/admin/taxonomy/keywords/{kw['id']}", headers=auth_headers, json={"standalone": False}).status_code == 403

    db_cursor.execute("update users set is_platform_admin = true where id = %s", (uid,))
    try:
        r = client.patch(f"/admin/taxonomy/keywords/{kw['id']}", headers=auth_headers, json={"standalone": False})
        assert r.status_code == 200, r.text
        db_cursor.execute("select standalone from capability_taxonomy_keywords where id = %s", (kw["id"],))
        assert db_cursor.fetchone()["standalone"] is False
        assert client.patch(f"/admin/taxonomy/keywords/{kw['id']}", headers=auth_headers, json={}).status_code == 422
        assert client.patch(f"/admin/taxonomy/keywords/{kw['id']}", headers=auth_headers, json={"weight": 9}).status_code == 422
        assert client.patch(f"/admin/taxonomy/keywords/{uuid.uuid4()}", headers=auth_headers, json={"weight": 2}).status_code == 404
    finally:
        db_cursor.execute("update capability_taxonomy_keywords set standalone = true where id = %s", (kw["id"],))


def test_flag_off_and_per_call_override_in_process(client, auth_headers, tenders):
    """The same product, matched in-process with the feature forced off and on: off must produce NO keyword-only match."""
    import asyncio
    import os
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.programme_matching import match_product_to_programmes

    product_id = _confirmed_product(client, auth_headers, ["SPARES.REPLACEMENT"], "spare parts and replacement parts supplier")
    dsn = os.environ.get("TEST_DB_DSN", "postgresql://postgres:postgres@localhost:5433/doi").replace("postgresql://", "postgresql+asyncpg://")

    async def run(flag):
        engine = create_async_engine(dsn)
        try:
            async with async_sessionmaker(engine)() as session:
                return await match_product_to_programmes(session, product_id, keyword_only=flag)
        finally:
            await engine.dispose()

    try:
        off = asyncio.run(run(False))
        on = asyncio.run(run(True))
        title = tenders["spares"][1]
        assert title not in {m["programme_name"] for m in off["matches"]}
        assert off["trace"]["keyword_only_enabled"] is False and off["trace"]["keyword_only_matches"] == 0
        assert title in {m["programme_name"] for m in on["matches"]}
        assert all(m["match_basis"] == "code" for m in off["matches"])
    finally:
        client.delete(f"/products/{product_id}", headers=auth_headers)
