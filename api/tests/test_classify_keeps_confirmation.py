"""
Re-running POST /products/{id}/classify must never undo an analyst's
confirmation. A real bug found building the "Run All 10 Engines"
pop-up (2026-10): the classify upsert set classified_by back to
'ai_suggested' on conflict and pointed the row at a fresh, unreviewed
evidence row, so clicking "Run Capability Classification" a second
time silently un-confirmed a capability a person had already reviewed.
"""

PRODUCT = {
    "name": "Tactical UAV Data Link",
    "description": "Secure UAV drone communication data link for tactical unmanned aircraft",
    "trl": 6,
}


def _capabilities(client, headers, product_id):
    return client.get(f"/products/{product_id}/capabilities", headers=headers).json()


def test_reclassifying_keeps_an_analyst_confirmed_capability_confirmed(client, auth_headers):
    product_id = client.post("/products", headers=auth_headers, json=PRODUCT).json()["id"]

    first = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    assert first.status_code == 200
    assert len(first.json()["candidates"]) >= 1

    capability_id = _capabilities(client, auth_headers, product_id)[0]["capability_id"]
    confirm = client.post(f"/products/{product_id}/capabilities/{capability_id}/confirm", headers=auth_headers)
    assert confirm.status_code == 200

    before = next(c for c in _capabilities(client, auth_headers, product_id) if c["capability_id"] == capability_id)
    assert before["classified_by"] == "analyst"
    assert before["reviewed_by"] is not None

    second = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    assert second.status_code == 200
    flagged = [c for c in second.json()["candidates"] if str(c["capability_id"]) == str(capability_id)]
    assert flagged and flagged[0]["already_confirmed"] is True

    after = next(c for c in _capabilities(client, auth_headers, product_id) if c["capability_id"] == capability_id)
    assert after["classified_by"] == "analyst"
    assert after["reviewed_by"] == before["reviewed_by"]
    assert after["claim"] == before["claim"]


def test_reclassifying_still_refreshes_an_unconfirmed_suggestion(client, auth_headers):
    product_id = client.post("/products", headers=auth_headers, json=PRODUCT).json()["id"]
    client.post(f"/products/{product_id}/classify", headers=auth_headers)
    again = client.post(f"/products/{product_id}/classify", headers=auth_headers)
    assert again.status_code == 200
    assert all(c["already_confirmed"] is False for c in again.json()["candidates"])
    assert all(c["classified_by"] == "ai_suggested" for c in _capabilities(client, auth_headers, product_id))
