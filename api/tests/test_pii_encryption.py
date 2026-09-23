"""
Integration tests for encryption-at-rest on a tenant's own GST/PAN/
TAN/IEC registration numbers (2026-09, app/pii_encryption.py) — these
four columns were plaintext in the database until now. Verifies both
the round trip (owner still sees the real value back) AND the actual
at-rest property (the raw DB row is genuinely not the plaintext).
"""

import os
import psycopg2
import psycopg2.extras
import uuid

# Same default/override pattern as conftest.py's own DB_DSN — this
# file needs a second, independent raw connection (to inspect the
# actual stored bytes, not assert through the API), so it can't just
# import conftest's private module-level constant.
DB_DSN = os.environ.get("TEST_DB_DSN", "postgresql://postgres:postgres@localhost:5433/doi")


def _raw_db_row(tenant_id):
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("select gst_number, pan_number, tan_number, iec_license from tenants where id = %s", (tenant_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def test_gst_number_round_trips_through_the_owners_own_profile(client, auth_headers):
    real_gst = "27AAAPL1234C1Z5"
    resp = client.patch("/company/profile", headers=auth_headers, json={"gst_number": real_gst})
    assert resp.status_code == 200
    assert resp.json()["gst_number"] == real_gst

    get_resp = client.get("/company/profile", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["gst_number"] == real_gst


def test_the_raw_database_value_is_not_the_plaintext(client, auth_headers):
    """The actual point of this feature — proven against the real column, not just the API's own round trip."""
    real_pan = "AAAPL1234C"
    patch_resp = client.patch("/company/profile", headers=auth_headers, json={"pan_number": real_pan})
    tenant_id = None
    # Extract tenant_id from the token, same helper pattern other test files use.
    import base64, json as jsonlib
    token = auth_headers["Authorization"].split(" ")[1]
    payload = jsonlib.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    tenant_id = payload["tenant_id"]

    raw = _raw_db_row(tenant_id)
    assert raw["pan_number"] != real_pan
    assert raw["pan_number"] is not None
    assert len(raw["pan_number"]) > len(real_pan)  # Fernet ciphertext is always longer than the plaintext


def test_all_four_fields_round_trip_independently(client, auth_headers):
    resp = client.patch(
        "/company/profile", headers=auth_headers,
        json={
            "gst_number": "27AAAPL1234C1Z5",
            "pan_number": "AAAPL1234C",
            "tan_number": "BLRT12345D",
            "iec_license": "0312345678",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["gst_number"] == "27AAAPL1234C1Z5"
    assert body["pan_number"] == "AAAPL1234C"
    assert body["tan_number"] == "BLRT12345D"
    assert body["iec_license"] == "0312345678"


def test_a_partial_update_does_not_disturb_an_unrelated_encrypted_field(client, auth_headers):
    client.patch("/company/profile", headers=auth_headers, json={"gst_number": "27AAAPL1234C1Z5"})
    client.patch("/company/profile", headers=auth_headers, json={"pan_number": "AAAPL1234C"})

    get_resp = client.get("/company/profile", headers=auth_headers)
    body = get_resp.json()
    assert body["gst_number"] == "27AAAPL1234C1Z5"
    assert body["pan_number"] == "AAAPL1234C"


def test_compliance_badges_still_work_without_decrypting_anything(client, auth_headers):
    """
    The public share-card route only needs presence (NOT NULL), never
    the real value — Fernet ciphertext is still non-NULL, so this
    must keep working unchanged.
    """
    client.patch("/company/profile", headers=auth_headers, json={"gst_number": "27AAAPL1234C1Z5"})
    profile = client.get("/company/profile", headers=auth_headers).json()
    share_token = profile["share_token"]

    card = client.get(f"/public/companies/{share_token}")
    assert card.status_code == 200
    assert card.json()["compliance_badges"]["gst"] is True
    assert card.json()["compliance_badges"]["pan"] is False


def test_a_different_registered_tenant_sees_the_real_decrypted_value_via_the_vetting_route(client, auth_headers):
    """
    GET /companies/{tenant_id}/profile is a deliberate cross-tenant
    read (any registered user, not just the owner) — it must decrypt
    too, not just the owner's own GET.
    """
    import base64, json as jsonlib
    token = auth_headers["Authorization"].split(" ")[1]
    payload = jsonlib.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    owner_tenant_id = payload["tenant_id"]

    client.patch("/company/profile", headers=auth_headers, json={"gst_number": "27AAAPL1234C1Z5"})

    viewer_email = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
    viewer_signup = client.post(
        "/auth/signup",
        json={"company_name": f"Viewer Co {uuid.uuid4().hex[:6]}", "full_name": "Viewer",
              "email": viewer_email, "password": "a-real-password-123"},
    )
    viewer_headers = {"Authorization": f"Bearer {viewer_signup.json()['access_token']}"}

    resp = client.get(f"/companies/{owner_tenant_id}/profile", headers=viewer_headers)
    assert resp.status_code == 200
    assert resp.json()["gst_number"] == "27AAAPL1234C1Z5"
