"""
Tests for the shared, race-safe organization upsert
(app/ingestion_common.py) added after a Report Intel review into
Customer Intelligence found two real duplicate pairs live —
db/migrations/035_organizations_dedup.sql.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.org_name_format import normalize_org_name  # noqa: E402


# --- pure normalization ------------------------------------------------

def test_collapses_repeated_whitespace():
    assert normalize_org_name("DLA  AVIATION AT OGDEN, UT") == "DLA AVIATION AT OGDEN, UT"


def test_trims_leading_and_trailing_whitespace():
    assert normalize_org_name("  Ministry of Defence  ") == "Ministry of Defence"


def test_leaves_an_already_clean_name_unchanged():
    assert normalize_org_name("ARMSCOR") == "ARMSCOR"


def test_does_not_fold_case_or_accents():
    """
    Deliberately conservative — see migration 035's header for why
    only whitespace is touched. Accent-folding or case-folding here
    would risk merging two differently-worded but genuinely DIFFERENT
    real organisations, which is worse than the fragmentation this
    fixes.
    """
    assert normalize_org_name("Ministère de la Défense") == "Ministère de la Défense"


# --- the real integrity guarantee: the DB-level constraint --------------

def test_unique_constraint_exists_on_name_and_org_type(db_cursor):
    db_cursor.execute("""
        select 1 from pg_constraint
        where conname = 'organizations_name_org_type_key'
    """)
    assert db_cursor.fetchone() is not None, "run db/migrations/035_organizations_dedup.sql"


def test_no_duplicate_organizations_exist(db_cursor):
    """
    The real, live problem this whole migration existed to fix:
    a Ukrainian military unit name was found inserted twice, byte-
    for-byte identical, and a SAM.gov DLA Aviation office was found
    inserted twice differing only by a double space. Both were merged
    by the migration; this pins that no (name, org_type) pair is ever
    duplicated going forward, across every source.
    """
    db_cursor.execute("""
        select name, org_type, count(*) from organizations
        group by name, org_type having count(*) > 1
    """)
    dupes = db_cursor.fetchall()
    assert dupes == [], f"found {len(dupes)} duplicate organization(s): {dupes[:3]}"


# --- end-to-end: two concurrent-shaped calls resolve to one row ---------

def test_repeated_ingestion_triggers_do_not_create_duplicate_buyers(client, auth_headers, db_cursor):
    """
    Real integration proof, not just a unique-constraint check:
    triggering the same source's ingestion twice in a row (which is
    exactly what a scheduled re-run does) must never grow the buyer
    count for organisations that already exist — the ON CONFLICT
    upsert must resolve to the SAME row both times.
    """
    db_cursor.execute("select count(*) as c from organizations where org_type = 'government_body'")
    before = db_cursor.fetchone()["c"]

    resp1 = client.post("/ingestion/colombia/run?days_back=5", headers=auth_headers, json={})
    assert resp1.status_code == 200
    db_cursor.execute("select count(*) as c from organizations where org_type = 'government_body'")
    after_first = db_cursor.fetchone()["c"]

    resp2 = client.post("/ingestion/colombia/run?days_back=5", headers=auth_headers, json={})
    assert resp2.status_code == 200
    db_cursor.execute("select count(*) as c from organizations where org_type = 'government_body'")
    after_second = db_cursor.fetchone()["c"]

    # The second run re-processes the exact same real buyers (same
    # 5-day window) — it must add zero NEW government_body rows.
    assert after_second == after_first, (
        f"second identical run grew government_body organizations from "
        f"{after_first} to {after_second} — the upsert is not idempotent"
    )
    assert after_first >= before
