"""
Proves the real point of the CPV mapping migration: a product
classified with a capability that has a CPV mapping now genuinely
matches a UK-sourced (CPV-coded) programme, not just a NAICS-coded
one. Uses a controlled fixture programme with a real verified CPV
code (35410000, armoured military vehicles), same deterministic-
fixture pattern as the existing Phase 3 tests — not dependent on
whatever's actually in the live-ingested UK data at test time.
"""

import uuid


def test_cpv_mapped_capability_matches_uk_sourced_programme(client, auth_headers, db_cursor):
    db_cursor.execute("select id from sources where name = 'UK Find a Tender Service'")
    source_row = db_cursor.fetchone()
    assert source_row is not None, (
        "UK Find a Tender source must be seeded — run db/migrations/009_uk_find_a_tender.sql first"
    )
    source_id = source_row["id"]

    fixture_ref = f"test-fixture-uk-{uuid.uuid4().hex}"
    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        ("Test Fixture: Armoured Vehicle Upgrade Programme", "United Kingdom",
         "rfp_issued", source_id, "35410000", fixture_ref),  # real verified CPV code, armoured military vehicles
    )
    programme_id = str(db_cursor.fetchone()["id"])

    create_resp = client.post(
        "/products", headers=auth_headers,
        json={"name": "Armoured Vehicle Platform", "description": "Land systems armoured vehicle upgrade kit", "trl": 7},
    )
    product_id = create_resp.json()["id"]

    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    land_candidate = next(
        (c for c in classify_resp.json()["candidates"] if c["code"] == "LAND.SYSTEMS"), None
    )
    assert land_candidate is not None, "expected LAND.SYSTEMS among classification candidates"

    confirm_resp = client.post(
        f"/products/{product_id}/capabilities/{land_candidate['capability_id']}/confirm",
        headers=auth_headers,
    )
    assert confirm_resp.status_code == 200

    match_resp = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers)
    assert match_resp.status_code == 200
    matches = match_resp.json()["matches"]

    fixture_match = next((m for m in matches if m["programme_id"] == programme_id), None)
    assert fixture_match is not None, (
        "expected the UK CPV-coded fixture programme to appear in matches — "
        "this is the actual proof CPV mapping connects UK data to Phase 3 matching"
    )
    assert fixture_match["naics_match"] is True  # field name kept for backward compat — see programme_matching.py
    assert fixture_match["matched_classification_code"] == "35410000"


def test_uav_capability_is_now_mapped_to_verified_cpv_codes(client, auth_headers, db_cursor):
    """
    This test used to assert the OPPOSITE — that UAV.INTEGRATION had
    no CPV mapping, because v1 found no code it could defend. It said
    to update it on the day a real, verified mapping was added rather
    than let it block the fix. That day is
    db/migrations/025_cpv_mapping_expansion.sql, where the codes were
    read back out of TED's own notice titles (TED prints the official
    CPV label in the title: "Belgium – Unmanned aerial vehicles – …").

    It now guards the other direction: that the mapping stays, and
    stays confined to codes that genuinely denote unmanned aircraft.
    """
    db_cursor.execute(
        """
        select tcm.cpv_code from taxonomy_cpv_mapping tcm
        join capability_taxonomy ct on ct.id = tcm.capability_id
        where ct.code = 'UAV.INTEGRATION'
        order by tcm.cpv_code
        """
    )
    codes = [r["cpv_code"] for r in db_cursor.fetchall()]
    assert codes == ["34711200", "35613000"], (
        "UAV.INTEGRATION must map to Non-piloted aircraft (34711200) and Unmanned aerial "
        "vehicles (35613000) — both verified against live TED notices"
    )


def test_uav_product_now_reaches_cpv_coded_award_data(client, auth_headers, db_cursor):
    """
    The point of the expansion, end to end: award winners are only
    extracted from TED and UK Find a Tender, both CPV-coded, so before
    025 a UAV product's Competitor Intelligence could not answer at
    all. It must now resolve against real award rows.
    """
    product_id = client.post(
        "/products", headers=auth_headers,
        json={"name": "Tactical UAV Platform", "description": "UAV integration, unmanned aerial system", "trl": 7},
    ).json()["id"]
    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    uav = next(c for c in candidates if c["code"] == "UAV.INTEGRATION")
    client.post(f"/products/{product_id}/capabilities/{uav['capability_id']}/confirm", headers=auth_headers)

    report = client.get(f"/products/{product_id}/intel-report", headers=auth_headers).json()
    competitor = next(e for e in report["engines"] if e["engine"] == "06")
    assert competitor["ran"] is True, competitor["note"]
    assert competitor["total_rows"] > 0


def test_generic_surveillance_cpv_is_not_mapped_to_defence_capabilities(db_cursor):
    """
    35120000 is CPV's CCTV/alarms/access-control branch (its children
    are Security cameras, Alarm systems, Metal detectors). v1 mapped
    it to seven capabilities at once, so a single building-CCTV tender
    scored the +3 code bonus against all seven — the same shape of
    false positive as "GPS HATCH SYSTEMS". Removed in
    db/migrations/026 once 025 had given each of them precise codes.
    """
    db_cursor.execute(
        """
        select ct.code from taxonomy_cpv_mapping m
        join capability_taxonomy ct on ct.id = m.capability_id
        where m.cpv_code = '35120000'
        """
    )
    assert [r["code"] for r in db_cursor.fetchall()] == []


def test_the_capabilities_that_lost_it_kept_precise_cpv_reach(db_cursor):
    """
    Removing the generic code must not leave any of those seven unable
    to reach CPV-coded data at all — which is exactly why it could not
    be removed before 025 added specific codes.
    """
    for code, expected in [
        ("SENSING.RADAR", "35722000"),          # Radar
        ("EW.GENERAL", "35730000"),             # Electronic warfare systems and counter measures
        ("ISR.GENERAL", "35720000"),            # ISTAR — the military branch, not 35120000 site security
        ("SENSORS.GENERAL", "35125100"),        # Sensors
        ("SENSOR.ELECTRO_OPTIC", "38651000"),   # Cameras
        ("CUAS.GENERAL", "35723000"),           # Air defence radar
        ("CYBER.DEFENCE", "48730000"),          # Security software package
    ]:
        db_cursor.execute(
            """
            select m.cpv_code from taxonomy_cpv_mapping m
            join capability_taxonomy ct on ct.id = m.capability_id
            where ct.code = %s
            """,
            (code,),
        )
        codes = [r["cpv_code"] for r in db_cursor.fetchall()]
        assert codes, f"{code} has no CPV mapping left at all"
        assert expected in codes, f"{code} lost its precise code {expected}"
