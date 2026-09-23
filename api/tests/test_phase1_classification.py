"""
Phase 1 integration tests — these hit the real running API and
real database, same philosophy as test_foundation_smoke.py: no
mocking, because the bugs that matter (missing migration, wrong
evidence linkage, RLS gaps) only show up against the real thing.

Prerequisite: docker compose up AND
  db/migrations/002_capability_keywords.sql already applied.
"""


def test_classify_uav_product_suggests_uav_capability(client, auth_headers):
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={
            "name": "Secure UAV Data Link",
            "description": "Encrypted communication, long-range, UAV integration",
            "trl": 7,
        },
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    assert classify_resp.status_code == 200
    candidates = classify_resp.json()["candidates"]

    assert len(candidates) > 0, "expected at least one classification candidate"
    codes = [c["code"] for c in candidates]
    # this product's text should trigger both UAV and secure-comms matches
    assert "UAV.INTEGRATION" in codes
    assert "COMMS.SECURE.TACTICAL" in codes

    # every candidate must carry real evidence, not just a label
    for c in candidates:
        assert c["matched_keywords"], f"{c['code']} has no matched_keywords — not explainable"
        assert c["confidence"] in ("high", "medium", "low")


def test_classify_unrelated_product_returns_no_candidates(client, auth_headers):
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Office Chair", "description": "Ergonomic seating with wheels", "trl": None},
    )
    product_id = create_resp.json()["id"]

    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    assert classify_resp.status_code == 200
    assert classify_resp.json()["candidates"] == [], (
        "classifier should not force a match onto genuinely unrelated text"
    )


def test_classified_product_appears_in_capabilities_list_as_ai_suggested(client, auth_headers):
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Tactical Radar System", "description": "Doppler radar for detection", "trl": 8},
    )
    product_id = create_resp.json()["id"]
    client.post(f"/products/{product_id}/classify", headers=auth_headers)

    list_resp = client.get(f"/products/{product_id}/capabilities", headers=auth_headers)
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) > 0
    assert all(r["classified_by"] == "ai_suggested" for r in rows), (
        "freshly classified capabilities must start as ai_suggested, never pre-confirmed"
    )
    # evidence must be attached, not null
    assert all(r["claim"] is not None for r in rows)


def test_confirm_marks_capability_as_analyst_reviewed(client, auth_headers):
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Jamming System", "description": "Electronic warfare jammer for spectrum denial", "trl": 6},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    candidates = classify_resp.json()["candidates"]
    assert candidates, "test requires at least one classified candidate to confirm"
    capability_id = candidates[0]["capability_id"]

    confirm_resp = client.post(
        f"/products/{product_id}/capabilities/{capability_id}/confirm", headers=auth_headers
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "confirmed"

    list_resp = client.get(f"/products/{product_id}/capabilities", headers=auth_headers)
    confirmed = next(r for r in list_resp.json() if r["capability_id"] == capability_id)
    assert confirmed["classified_by"] == "analyst"
    assert confirmed["reviewed_by"] is not None
    assert confirmed["reviewed_at"] is not None


def test_reject_removes_capability_link(client, auth_headers):
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Naval Sonar Array", "description": "Submarine sonar detection system", "trl": 7},
    )
    product_id = create_resp.json()["id"]
    classify_resp = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    candidates = classify_resp.json()["candidates"]
    assert candidates
    capability_id = candidates[0]["capability_id"]

    reject_resp = client.delete(
        f"/products/{product_id}/capabilities/{capability_id}", headers=auth_headers
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    list_resp = client.get(f"/products/{product_id}/capabilities", headers=auth_headers)
    remaining_ids = [r["capability_id"] for r in list_resp.json()]
    assert capability_id not in remaining_ids


def test_confirm_nonexistent_capability_returns_404(client, auth_headers):
    create_resp = client.post(
        "/products", headers=auth_headers, json={"name": "Blank Product", "description": "", "trl": 1}
    )
    product_id = create_resp.json()["id"]
    fake_capability_id = "00000000-0000-0000-0000-000000000000"

    resp = client.post(
        f"/products/{product_id}/capabilities/{fake_capability_id}/confirm", headers=auth_headers
    )
    assert resp.status_code == 404


def test_capabilities_are_tenant_isolated(client, auth_headers):
    # tenant A classifies a product
    create_resp = client.post(
        "/products",
        headers=auth_headers,
        json={"name": "Cyber Defence Platform", "description": "Network intrusion detection cybersecurity", "trl": 7},
    )
    product_id = create_resp.json()["id"]
    client.post(f"/products/{product_id}/classify", headers=auth_headers)

    # tenant B should not even be able to see the product exists,
    # let alone its capabilities (RLS on `products` blocks the
    # lookup inside the classify/list routes at the source)
    import uuid
    email_b = f"tenant-cap-b-{uuid.uuid4().hex[:10]}@example.com"
    signup_b = client.post(
        "/auth/signup",
        json={"company_name": "Rival Corp", "full_name": "Test Admin", "email": email_b, "password": "another-real-password-1"},
    )
    token_b = signup_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    list_resp = client.get(f"/products/{product_id}/capabilities", headers=headers_b)
    # RLS makes the product invisible to tenant B, so the capability
    # list for it comes back empty rather than erroring — either way,
    # tenant B must see nothing of tenant A's classification.
    assert list_resp.status_code == 200
    assert list_resp.json() == []
