"""
Integration tests for /ingestion/colombia/run, registry presence, and
the query this source sends. The live-network test is opt-in, like
every other source's.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.colombia_ingestion import build_where  # noqa: E402


def test_colombia_trigger_requires_admin(client):
    resp = client.post("/ingestion/colombia/run", json={})
    assert resp.status_code in (401, 403)


def test_colombia_appears_in_sources_status(client, auth_headers):
    resp = client.get("/ingestion/sources/status", headers=auth_headers)
    assert resp.status_code == 200
    entry = next((s for s in resp.json()["sources"] if s["code"] == "colombia"), None)
    assert entry is not None, "colombia should be registered in the ingestion source registry"
    assert entry["display_name"] == "SECOP II (Colombia Compra Eficiente)"
    assert entry["scheduled"] is True
    # An app token only raises the rate limit — this source must be
    # schedulable with no credentials at all, so it must never be
    # reported as waiting on a key.
    assert entry["api_key_configured"] is True


def test_the_source_row_exists_with_its_terms_recorded(db_cursor):
    db_cursor.execute(
        "select source_type, trust_level, url, terms_notes from sources where name = %s",
        ("SECOP II (Colombia Compra Eficiente)",),
    )
    row = db_cursor.fetchone()
    assert row is not None, "run db/migrations/027_colombia_source.sql"
    assert row["source_type"] == "government_portal"
    assert "datos.gov.co" in row["url"]
    # The licence/access position has to travel with the source row,
    # same as every other source here.
    assert "no API key required" in row["terms_notes"]


def test_the_server_side_query_filters_by_date_and_buyer():
    """
    The SoQL `where` is part of this source's contract: it is what
    keeps a run from transferring the whole of Colombian public
    procurement. It is a pre-filter only — colombia_normalize
    re-checks every row it returns.
    """
    where = build_where("2026-08-01")
    assert "fecha_de_publicacion_del > '2026-08-01T00:00:00'" in where
    assert "MINISTERIO DE DEFENSA NACIONAL" in where
    assert "ARMADA NACIONAL" in where
    # Must be upper()-folded on the server side, since the column is
    # not consistently cased.
    assert "upper(entidad)" in where


def test_ingested_colombian_programmes_carry_unspsc_and_a_link(db_cursor):
    """
    The two things that make this source worth having: a real
    classification code that feeds matching, and a working link to the
    notice. Skips when no run has happened yet rather than failing —
    the trigger itself is opt-in.
    """
    db_cursor.execute(
        """
        select count(*) as total,
               count(p.naics_code) as coded,
               count(p.ui_link) as linked
        from programmes p join sources s on s.id = p.source_id
        where s.name = 'SECOP II (Colombia Compra Eficiente)'
        """
    )
    row = db_cursor.fetchone()
    if row["total"] == 0:
        pytest.skip("no Colombian ingestion has run against this database yet")
    assert row["linked"] == row["total"], "every SECOP II row publishes a notice URL"
    assert row["coded"] / row["total"] > 0.8, "most rows should carry a usable UNSPSC code"


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_COLOMBIA_TEST"),
    reason="Set RUN_LIVE_COLOMBIA_TEST=1 to run this — it hits the live datos.gov.co API.",
)
def test_colombia_trigger_response_shape(client, auth_headers):
    resp = client.post("/ingestion/colombia/run?days_back=30", headers=auth_headers, json={})
    assert resp.status_code == 200
    body = resp.json()
    for field in ("job_id", "records_ingested", "awards_recorded", "rows_examined", "date_from"):
        assert field in body, field
    print(
        f"\nColombia ingestion: {body['records_ingested']} programmes, "
        f"{body['awards_recorded']} awards, from {body['rows_examined']} rows examined"
    )
