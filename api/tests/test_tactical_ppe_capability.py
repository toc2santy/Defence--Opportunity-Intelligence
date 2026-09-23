"""
Integration tests for the TACTICAL.PROTECTIVE_EQUIPMENT capability
(migrations 033/034) — the largest coherent real gap found in a
Report Intel taxonomy review, plus the plural-keyword bug live
testing caught immediately after adding it.
"""


def test_capability_exists_with_full_mapping_coverage(db_cursor):
    db_cursor.execute(
        "select id from capability_taxonomy where code = 'TACTICAL.PROTECTIVE_EQUIPMENT'"
    )
    row = db_cursor.fetchone()
    assert row is not None, "run db/migrations/033_tactical_protective_equipment.sql"
    cap_id = row["id"]

    db_cursor.execute("select cpv_code from taxonomy_cpv_mapping where capability_id = %s", (cap_id,))
    cpv_codes = {r["cpv_code"] for r in db_cursor.fetchall()}
    assert cpv_codes == {"35815100", "35813000", "35812000", "35811300", "35113400"}

    db_cursor.execute("select naics_code from taxonomy_naics_mapping where capability_id = %s", (cap_id,))
    assert {r["naics_code"] for r in db_cursor.fetchall()} == {"339113"}


def test_plural_forms_are_present_after_the_034_fix(db_cursor):
    """
    Pins the actual bug found by live-testing this capability: TED
    prints these CPV labels plural 100% of the time in this
    platform's own ingested data, and the word-boundary regex in
    app/scoring.py does not match a singular keyword inside a plural
    word. Both forms must exist.
    """
    db_cursor.execute("""
        select k.keyword from capability_taxonomy_keywords k
        join capability_taxonomy ct on ct.id = k.capability_id
        where ct.code = 'TACTICAL.PROTECTIVE_EQUIPMENT'
    """)
    keywords = {r["keyword"] for r in db_cursor.fetchall()}
    for singular, plural in [
        ("military helmet", "military helmets"),
        ("bullet-proof vest", "bullet-proof vests"),
        ("combat uniform", "combat uniforms"),
        ("military uniform", "military uniforms"),
    ]:
        assert singular in keywords, singular
        assert plural in keywords, plural


def test_product_classifies_and_matches_with_high_confidence(client, auth_headers):
    """
    End-to-end proof, not just a unit check on the keyword table: a
    product described in plain terms for this capability must both
    classify AND produce real high-confidence matches against already-
    ingested TED data — the exact test that caught the plural bug live.
    """
    product_id = client.post(
        "/products", headers=auth_headers,
        json={
            "name": "Tactical Body Armor",
            "description": "bullet-proof vest, ballistic protection, combat uniform, military helmet manufacturer",
            "trl": 9,
        },
    ).json()["id"]

    candidates = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
    ppe = next((c for c in candidates if c["code"] == "TACTICAL.PROTECTIVE_EQUIPMENT"), None)
    assert ppe is not None, "product should classify as TACTICAL.PROTECTIVE_EQUIPMENT"

    client.post(f"/products/{product_id}/capabilities/{ppe['capability_id']}/confirm", headers=auth_headers)
    matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]

    high = [m for m in matches if m["confidence"] == "high"]
    assert len(high) > 0, (
        "expected real high-confidence matches against live TED data — "
        "0 here would mean the plural-keyword regression came back"
    )
