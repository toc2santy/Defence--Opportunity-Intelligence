"""
Data-health checks through the real API, on a throwaway source: an 'open' tender that
already has an award recorded must be reported as an ERROR for that source (the exact
shape of the SECOP II / Paraguay bugs), and a clean source must not be.
"""

import base64
import json
import uuid

import pytest


def _user_id(headers):
    p = headers["Authorization"].split(" ")[1].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["sub"]


@pytest.fixture
def two_sources(db_cursor):
    tag = uuid.uuid4().hex[:8]
    bad, good = f"Test Health Bad {tag}", f"Test Health Good {tag}"
    ids = {}
    for name in (bad, good):
        db_cursor.execute("insert into sources (name, source_type) values (%s, 'government_portal') returning id", (name,))
        ids[name] = db_cursor.fetchone()["id"]
    db_cursor.execute("insert into organizations (name, org_type, country) values (%s, 'oem', 'Testland') returning id", (f"Test Winner {tag}",))
    org = db_cursor.fetchone()["id"]

    prog = {}
    for name, stage in ((bad, "rfp_issued"), (good, "contract_awarded")):
        db_cursor.execute(
            """insert into programmes (name, country, stage, source_id, external_ref, ui_link, response_deadline)
               values (%s, 'Testland', %s, %s, %s, 'https://example.org/t', '2030-01-01T00:00:00') returning id""",
            (f"Health fixture {name}", stage, ids[name], f"hf-{uuid.uuid4().hex}"),
        )
        prog[name] = db_cursor.fetchone()["id"]
        db_cursor.execute("insert into contract_awards (programme_id, winner_organization_id, source_id) values (%s, %s, %s)",
                          (prog[name], org, ids[name]))
    yield bad, good
    db_cursor.execute("delete from data_health_results where source_name in (%s, %s)", (bad, good))
    db_cursor.execute("delete from programmes where source_id = any(%s::uuid[])", (list(ids.values()),))
    db_cursor.execute("delete from organizations where id = %s", (org,))
    db_cursor.execute("delete from sources where id = any(%s::uuid[])", (list(ids.values()),))


def test_open_tender_with_an_award_is_flagged_and_a_consistent_source_is_not(client, auth_headers, db_cursor, two_sources):
    bad, good = two_sources
    db_cursor.execute("update users set is_platform_admin = true where id = %s", (_user_id(auth_headers),))

    run = client.post("/admin/data-health/run", headers=auth_headers)
    assert run.status_code == 200, run.text
    assert run.json()["errors"] >= 1

    latest = {s["source"]: s for s in client.get("/admin/data-health", headers=auth_headers).json()["sources"]}
    bad_checks = {c["check"]: c for c in latest[bad]["checks"]}
    assert latest[bad]["status"] == "error"
    assert bad_checks["awarded_but_open"]["severity"] == "error" and bad_checks["awarded_but_open"]["affected"] == 1
    assert "award" in bad_checks["awarded_but_open"]["message"]
    assert bad_checks["awarded_but_open"]["detail"]["examples"], "an error must show example tenders"

    good_checks = {c["check"]: c for c in latest[good]["checks"]}
    assert good_checks["awarded_but_open"]["severity"] == "ok"
    assert good_checks["malformed_link"]["severity"] == "ok"


def test_data_health_and_reconcile_need_platform_admin(client, auth_headers):
    assert client.get("/admin/data-health", headers=auth_headers).status_code == 403
    assert client.post("/admin/data-health/run", headers=auth_headers).status_code == 403
    assert client.post("/admin/source-reconcile/run", headers=auth_headers).status_code == 403


def test_reconcile_reports_sources_it_cannot_check_instead_of_pretending(client, auth_headers, db_cursor):
    db_cursor.execute("update users set is_platform_admin = true where id = %s", (_user_id(auth_headers),))
    r = client.post("/admin/source-reconcile/run?source=SAM.gov%20Contract%20Opportunities%20API&dry_run=true", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"status": "not_reconcilable", "source": "SAM.gov Contract Opportunities API"}
