"""
Integration tests for GET /intelligence/customers and
/intelligence/customers/{id}. Hits the live API over HTTP, same as
every other *_trigger.py test — no mocking (see CLAUDE.md).
"""


def test_customers_requires_auth(client):
    resp = client.get("/intelligence/customers")
    assert resp.status_code in (401, 403)


def test_customers_list_shape(client, auth_headers):
    resp = client.get("/intelligence/customers", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "customers" in body
    customers = body["customers"]
    assert isinstance(customers, list)
    # Real ingested data should already be present from earlier
    # sources (SAM.gov, UK FT, TED, CPPP, CanadaBuys all write
    # government_body organizations) — this is read-only shared
    # reference data, not something this test needs to seed itself.
    assert len(customers) > 0

    top = customers[0]
    for key in ("organization_id", "organization_name", "country",
                "programme_count", "sources", "top_capabilities"):
        assert key in top


def test_customers_sorted_by_programme_count_descending(client, auth_headers):
    resp = client.get("/intelligence/customers", headers=auth_headers)
    counts = [c["programme_count"] for c in resp.json()["customers"]]
    assert counts == sorted(counts, reverse=True)


def test_customer_detail_matches_list_entry(client, auth_headers):
    customers = client.get("/intelligence/customers", headers=auth_headers).json()["customers"]
    top = customers[0]

    resp = client.get(f"/intelligence/customers/{top['organization_id']}", headers=auth_headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["organization_id"] == top["organization_id"]
    assert detail["organization_name"] == top["organization_name"]
    assert detail["programme_count"] == top["programme_count"]
    assert len(detail["programmes"]) == detail["programme_count"]

    for programme in detail["programmes"]:
        for key in ("programme_id", "name", "stage", "classification_code",
                    "capability_label", "response_deadline", "source_name"):
            assert key in programme


def test_customer_detail_404_for_unknown_id(client, auth_headers):
    resp = client.get(
        "/intelligence/customers/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert resp.status_code == 404
