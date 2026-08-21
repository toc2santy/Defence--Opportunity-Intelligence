def test_key_status_endpoint_reflects_seeded_expiry(client, auth_headers):
    resp = client.get("/ingestion/sam-gov/key-status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_at"] == "2026-11-15"
    assert body["status"] in ("ok", "warning", "expired")  # depends on when this test actually runs
    assert "days_remaining" in body


def test_key_status_update_requires_admin(client):
    # a totally unauthenticated request should be rejected
    import httpx
    resp = httpx.patch(
        "http://localhost:8000/ingestion/sam-gov/key-status",
        json={"expires_at": "2027-01-01"},
    )
    assert resp.status_code in (401, 403)


def test_key_status_update_changes_the_record(client, auth_headers):
    resp = client.patch(
        "/ingestion/sam-gov/key-status",
        headers=auth_headers,
        json={"expires_at": "2027-06-01", "notes": "Renewed during a test run"},
    )
    assert resp.status_code == 200
    assert resp.json()["expires_at"] == "2027-06-01"

    # and it should persist
    check_resp = client.get("/ingestion/sam-gov/key-status", headers=auth_headers)
    assert check_resp.json()["expires_at"] == "2027-06-01"

    # restore it so this test doesn't leave the real tracked expiry
    # date wrong for actual use after the test suite runs
    client.patch(
        "/ingestion/sam-gov/key-status",
        headers=auth_headers,
        json={"expires_at": "2026-11-15", "notes": "Restored after test"},
    )
