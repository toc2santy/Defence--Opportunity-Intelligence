"""
Integration tests for the self-expanding taxonomy (2026-10, migration 052)
through the real API: a platform admin runs the learner, high-precision
keywords are added automatically at weight 1, weaker ones wait for review,
and a removed learned keyword is never learned again.

Everything seeded here (a throwaway capability, its code mapping, 12
tenders, 10 buying organisations) is deleted at the end so the shared
reference data is left exactly as found.
"""

import uuid

import pytest


def _user_id(headers):
    import base64, json
    payload = headers["Authorization"].split(" ")[1].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]


def _letters(n=9):
    return "".join(chr(97 + int(c, 16)) for c in uuid.uuid4().hex[:n]) + "x"


@pytest.fixture
def seeded(client, auth_headers, db_cursor):
    db_cursor.execute("update users set is_platform_admin = true where id = %s", (_user_id(auth_headers),))
    code = f"ZZ.LEARN.{uuid.uuid4().hex[:8].upper()}"
    r = client.post("/admin/taxonomy", headers=auth_headers,
                    json={"code": code, "label": "Learning Test Capability", "sector": "Learning Test"})
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    naics = "9" + str(uuid.uuid4().int)[:5]
    db_cursor.execute("insert into taxonomy_naics_mapping (capability_id, naics_code) values (%s, %s)", (cap_id, naics))

    strong, weak = _letters(), _letters()
    org_ids = []
    for i in range(10):
        db_cursor.execute(
            "insert into organizations (name, org_type, country) values (%s, 'government_body', 'Testland') returning id",
            (f"Learning Test Buyer {uuid.uuid4().hex[:8]}",),
        )
        org_ids.append(db_cursor.fetchone()["id"])
    prog_ids = []
    for i in range(12):
        # `strong` appears in all 12 tenders (-> automatic tier); `weak` in only
        # the first 6 (-> review queue).
        title = f"{strong} {weak if i < 6 else 'bracket'} unit {chr(97 + i) * 4}"
        db_cursor.execute(
            """insert into programmes (name, country, stage, naics_code, organization_id, external_ref)
               values (%s, 'Testland', 'rfp_issued', %s, %s, %s) returning id""",
            (title, naics, org_ids[i % 10], f"test-fixture-learn-{uuid.uuid4().hex}"),
        )
        prog_ids.append(db_cursor.fetchone()["id"])

    yield {"cap_id": cap_id, "code": code, "strong": strong, "weak": weak}

    db_cursor.execute("delete from programmes where id = any(%s::uuid[])", (prog_ids,))
    db_cursor.execute("delete from organizations where id = any(%s::uuid[])", (org_ids,))
    db_cursor.execute("delete from capability_taxonomy where id = %s", (cap_id,))   # cascades keywords/mapping/suggestions


def _keywords(db_cursor, cap_id):
    db_cursor.execute("select id, keyword, weight, origin from capability_taxonomy_keywords where capability_id = %s", (cap_id,))
    return {r["keyword"]: r for r in db_cursor.fetchall()}


def test_learner_adds_strong_keywords_automatically_and_queues_weak_ones(client, auth_headers, db_cursor, seeded):
    cap_id = seeded["cap_id"]

    preview = client.post("/admin/taxonomy/learn/run?dry_run=true", headers=auth_headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] is True
    assert _keywords(db_cursor, cap_id) == {}, "a dry run must change nothing"

    run = client.post("/admin/taxonomy/learn/run", headers=auth_headers)
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "succeeded"

    kws = _keywords(db_cursor, cap_id)
    auto = [k for k in kws if seeded["strong"] in k]
    assert auto, f"expected an automatic keyword containing {seeded['strong']}, got {list(kws)}"
    assert all(kws[k]["weight"] == 1 and kws[k]["origin"] == "learned" for k in kws)

    pending = client.get("/admin/taxonomy/suggestions?status_filter=pending&kind=keyword&limit=500", headers=auth_headers).json()
    mine = [p for p in pending if p["capability_code"] == seeded["code"]]
    assert any(seeded["weak"] in p["value"] for p in mine), "weaker phrase should wait for review"
    assert all(p["examples"] for p in mine), "every suggestion must show example tenders"


def test_approve_pending_keyword_with_chosen_weight_and_reject_is_permanent(client, auth_headers, db_cursor, seeded):
    cap_id = seeded["cap_id"]
    client.post("/admin/taxonomy/learn/run", headers=auth_headers)
    pending = [p for p in client.get("/admin/taxonomy/suggestions?status_filter=pending&kind=keyword&limit=500", headers=auth_headers).json()
               if p["capability_code"] == seeded["code"]]
    assert pending

    approved = pending[0]
    r = client.post(f"/admin/taxonomy/suggestions/{approved['id']}/approve", headers=auth_headers, json={"weight": 3})
    assert r.status_code == 200, r.text
    row = _keywords(db_cursor, cap_id)[approved["value"]]
    assert row["weight"] == 3 and row["origin"] == "learned"
    assert client.post(f"/admin/taxonomy/suggestions/{approved['id']}/approve", headers=auth_headers, json={}).status_code == 409

    # Removing a learned keyword marks it rejected, so the next run never re-adds it.
    removed_kw = next(k for k in _keywords(db_cursor, cap_id).values() if k["keyword"] != approved["value"])
    assert client.delete(f"/admin/taxonomy/keywords/{removed_kw['id']}", headers=auth_headers).status_code == 200
    client.post("/admin/taxonomy/learn/run", headers=auth_headers)
    assert removed_kw["keyword"] not in _keywords(db_cursor, cap_id)
    rejected = client.get("/admin/taxonomy/suggestions?status_filter=rejected&limit=500", headers=auth_headers).json()
    assert any(p["value"] == removed_kw["keyword"] and p["capability_code"] == seeded["code"] for p in rejected)


def test_rejecting_an_auto_added_keyword_retracts_it(client, auth_headers, db_cursor, seeded):
    cap_id = seeded["cap_id"]
    client.post("/admin/taxonomy/learn/run", headers=auth_headers)
    auto = [p for p in client.get("/admin/taxonomy/suggestions?status_filter=auto_added&limit=500", headers=auth_headers).json()
            if p["capability_code"] == seeded["code"]]
    assert auto
    assert client.post(f"/admin/taxonomy/suggestions/{auto[0]['id']}/reject", headers=auth_headers).status_code == 200
    assert auto[0]["value"] not in _keywords(db_cursor, cap_id)


def test_learning_routes_require_platform_admin(client, auth_headers):
    # auth_headers belongs to an ordinary tenant admin (not platform admin).
    assert client.post("/admin/taxonomy/learn/run", headers=auth_headers).status_code == 403
    assert client.get("/admin/taxonomy/suggestions", headers=auth_headers).status_code == 403
    fake = str(uuid.uuid4())
    assert client.post(f"/admin/taxonomy/suggestions/{fake}/approve", headers=auth_headers, json={}).status_code == 403
    assert client.post(f"/admin/taxonomy/suggestions/{fake}/reject", headers=auth_headers).status_code == 403
