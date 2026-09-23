"""
Integration tests for /ingestion/prozorro/run and registry presence.
The live-network test is opt-in, like every other source's.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.prozorro_ingestion import PROZORRO_LIST_PAGE_SIZE  # noqa: E402


def test_prozorro_trigger_requires_admin(client):
    resp = client.post("/ingestion/prozorro/run", json={})
    assert resp.status_code in (401, 403)


def test_prozorro_appears_in_sources_status(client, auth_headers):
    resp = client.get("/ingestion/sources/status", headers=auth_headers)
    assert resp.status_code == 200
    entry = next((s for s in resp.json()["sources"] if s["code"] == "prozorro"), None)
    assert entry is not None, "prozorro should be registered in the ingestion source registry"
    assert entry["display_name"] == "ProZorro (Ukraine Public Procurement)"
    assert entry["scheduled"] is True
    assert entry["api_key_configured"] is True  # no key needed at all


def test_the_source_row_exists_with_its_terms_recorded(db_cursor):
    db_cursor.execute(
        "select source_type, trust_level, url, terms_notes from sources where name = %s",
        ("ProZorro (Ukraine Public Procurement)",),
    )
    row = db_cursor.fetchone()
    assert row is not None, "run db/migrations/030_prozorro_source.sql"
    assert row["source_type"] == "government_portal"
    assert "openprocurement.org" in row["url"]
    assert "open for reuse" in row["terms_notes"]


def test_list_page_size_is_positive_and_sane():
    # Just a guard against an accidental typo turning this into 0 or
    # something absurd — the real value (500) was confirmed live to
    # work without erroring.
    assert 0 < PROZORRO_LIST_PAGE_SIZE <= 1000


def test_ingested_ukrainian_programmes_carry_a_real_link_and_contact(db_cursor):
    """
    Skips when no run has happened yet — the trigger itself is
    opt-in — rather than failing.
    """
    db_cursor.execute(
        """
        select count(*) as total, count(p.ui_link) as linked, count(p.contact_email) as with_email
        from programmes p join sources s on s.id = p.source_id
        where s.name = 'ProZorro (Ukraine Public Procurement)'
        """
    )
    row = db_cursor.fetchone()
    if row["total"] == 0:
        pytest.skip("no ProZorro ingestion has run against this database yet")
    assert row["linked"] == row["total"], "every ingested tender must carry the prozorro.gov.ua link"
    assert row["with_email"] > 0, "contactPoint was confirmed live to be reliably populated on this feed"


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_PROZORRO_TEST"),
    reason="Set RUN_LIVE_PROZORRO_TEST=1 to run this — it hits the live ProZorro API.",
)
def test_prozorro_trigger_response_shape(client, auth_headers):
    resp = client.post("/ingestion/prozorro/run", headers=auth_headers, json={})
    assert resp.status_code == 200
    body = resp.json()
    for field in ("job_id", "records_ingested", "awards_recorded", "candidates_found"):
        assert field in body, field
    print(f"\nProZorro ingestion: {body['records_ingested']} programmes from {body['candidates_found']} candidates")
