"""
Live test for app/sam_gov_refresh.py — hits the REAL sam.gov detail
endpoint (no SAM_GOV_API_KEY needed, see that module's own docstring
for why), so this only runs with real internet access. Skipped
automatically if sam.gov isn't reachable, same "opt-in, never breaks
a normal offline pytest run" spirit as test_sam_gov_live.py, just
gated on network reachability instead of an API key since this
feature specifically needs none.
"""

import httpx
import pytest


def _sam_gov_reachable() -> bool:
    try:
        resp = httpx.get(
            "https://sam.gov/api/prod/opps/v2/opportunities/c5a57833b0724671a664c6078e3d06dc",
            timeout=10.0,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _sam_gov_reachable(),
    reason="sam.gov not reachable from this environment — skipping live refresh test.",
)


def test_real_wapa_skydio_notice_has_a_real_contact_via_the_detail_endpoint():
    """
    The exact real notice this whole feature was built for (2026-09,
    user-reported): "15--Skydio X10 Drone and Starter Kit for WAPA".
    Confirms the detail endpoint still returns its real point of
    contact — a live, non-mocked proof this isn't reading stale/cached
    data, since sam.gov could in principle have changed or removed it
    since.
    """
    resp = httpx.get(
        "https://sam.gov/api/prod/opps/v2/opportunities/c5a57833b0724671a664c6078e3d06dc",
        timeout=15.0,
    )
    assert resp.status_code == 200
    poc = resp.json().get("data2", {}).get("pointOfContact") or []
    assert len(poc) >= 1
    assert poc[0].get("email")


def test_refresh_sam_gov_contacts_updates_a_seeded_stale_programme(db_cursor):
    """
    Real integration proof, not just an HTTP call: seeds a programme
    row shaped exactly like a real stale one (a real SAM.gov
    external_ref, contact fields null), runs the actual
    refresh_sam_gov_contacts() function against the real dev-stack
    database, and confirms it gets filled in for real.
    """
    import asyncio
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.sam_gov_refresh import refresh_sam_gov_contacts

    db_cursor.execute("select id from sources where name = 'SAM.gov Contract Opportunities API'")
    source_row = db_cursor.fetchone()
    if source_row is None:
        pytest.skip("SAM.gov source not seeded on this stack")
    source_id = source_row["id"]

    real_external_ref = "c5a57833b0724671a664c6078e3d06dc"
    # A real row with this exact external_ref may already exist on
    # this stack (from a real ingestion run) — (source_id,
    # external_ref) is uniquely indexed, so seeding a fresh row here
    # would collide. Delete it first rather than reuse-and-clear it,
    # so this test owns the row it asserts on and cleans up after
    # itself either way.
    db_cursor.execute(
        "delete from programmes where source_id = %s and external_ref = %s",
        (source_id, real_external_ref),
    )

    db_cursor.execute(
        """
        insert into programmes (name, country, stage, source_id, naics_code, external_ref, last_updated)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            "Test Fixture: Skydio Refresh Check", "United States", "rfp_issued", source_id,
            "336411", real_external_ref,
            # Deliberately ancient — refresh_sam_gov_contacts orders
            # by last_updated ASC (oldest first), and this stack may
            # already hold other null-contact SAM.gov fixture rows
            # from other tests; this guarantees this seeded row is
            # first in line regardless, without needing a batch_limit
            # large enough to also re-fetch every one of those (slow,
            # and pointless real network calls for fake external_refs).
            "2000-01-01T00:00:00Z",
        ),
    )
    programme_id = str(db_cursor.fetchone()["id"])

    test_db_dsn = os.environ.get("TEST_DB_DSN", "postgresql://postgres:postgres@localhost:5433/doi")
    async_dsn = test_db_dsn.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(async_dsn)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async def run():
        async with SessionLocal() as session:
            return await refresh_sam_gov_contacts(session, batch_limit=5)

    result = asyncio.run(run())
    assert result["status"] == "succeeded"

    db_cursor.execute("select contact_name, contact_email from programmes where id = %s", (programme_id,))
    row = db_cursor.fetchone()
    assert row["contact_email"] == "aumiller@wapa.gov"

    db_cursor.execute("delete from programmes where id = %s", (programme_id,))
