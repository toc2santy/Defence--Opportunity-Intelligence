"""
Integration tests for GET /intelligence/markets. Hits the live API
over HTTP, same as every other *_trigger.py test.
"""


def test_markets_requires_auth(client):
    resp = client.get("/intelligence/markets")
    assert resp.status_code in (401, 403)


def test_markets_shape(client, auth_headers):
    resp = client.get("/intelligence/markets", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "markets" in body
    assert isinstance(body["markets"], list)
    if body["markets"]:
        m = body["markets"][0]
        for key in ("country", "programme_count", "buyer_count", "sources", "stage_breakdown", "top_capabilities"):
            assert key in m


def test_markets_ranked_by_programme_count_descending(client, auth_headers):
    body = client.get("/intelligence/markets", headers=auth_headers).json()
    counts = [m["programme_count"] for m in body["markets"]]
    assert counts == sorted(counts, reverse=True)


def test_markets_no_country_appears_twice(client, auth_headers):
    body = client.get("/intelligence/markets", headers=auth_headers).json()
    countries = [m["country"] for m in body["markets"]]
    assert len(countries) == len(set(countries))


def test_markets_does_not_fabricate_a_score(client, auth_headers):
    """
    The page this powers used to show an invented 0-100 'attractiveness
    score' with no traceable source. Pinned here so nobody adds one
    back without a real, sourced basis for it.
    """
    body = client.get("/intelligence/markets", headers=auth_headers).json()
    for m in body["markets"]:
        assert "score" not in m
        assert "attractiveness_score" not in m
        assert "entry" not in m
        assert "entry_complexity" not in m
