"""
Integration tests for company-name/identity editability and the
LinkedIn URL field — added after a real reported bug: a company name
saved with a typo ("M/s Alpha-Elsec Lth" instead of "Ltd") had no way
to be corrected, because `name` was never in the editable model.
"""

import uuid


def _headers(client):
    email = f"cpid-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/signup",
        json={"company_name": "M/s Alpha-Elsec Lth", "full_name": "Test Admin",
              "email": email, "password": "a-real-password-123"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_the_exact_reported_typo_can_be_fixed(client):
    headers = _headers(client)
    before = client.get("/company/profile", headers=headers).json()
    assert before["name"] == "M/s Alpha-Elsec Lth"

    fixed = client.patch("/company/profile", headers=headers, json={"name": "M/s Alpha-Elsec Ltd"})
    assert fixed.status_code == 200
    assert fixed.json()["name"] == "M/s Alpha-Elsec Ltd"

    after = client.get("/company/profile", headers=headers).json()
    assert after["name"] == "M/s Alpha-Elsec Ltd"


def test_blank_name_is_rejected_not_silently_ignored(client):
    headers = _headers(client)
    resp = client.patch("/company/profile", headers=headers, json={"name": "   "})
    assert resp.status_code == 422


def test_website_country_phone_are_all_editable(client):
    headers = _headers(client)
    resp = client.patch(
        "/company/profile", headers=headers,
        json={"website": "https://alpha-elsec.example", "country": "India", "phone": "+91 98765 43210"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["website"] == "https://alpha-elsec.example"
    assert body["country"] == "India"
    assert body["phone"] == "+91 98765 43210"


def test_email_is_not_an_editable_field_on_this_route(client):
    """
    The one thing deliberately left out — login credentials live on
    `users`, not `tenants`, and this route never touches them. Sending
    an "email" key must simply be ignored (Pydantic drops unknown
    fields), not error and not change anything auth-related.
    """
    headers = _headers(client)
    resp = client.patch("/company/profile", headers=headers, json={"email": "hijack@example.com"})
    assert resp.status_code == 200
    me = client.get("/auth/me", headers=headers).json()
    assert me["email"] != "hijack@example.com"


# --- LinkedIn URL -----------------------------------------------------

def test_valid_linkedin_company_url_is_accepted(client):
    headers = _headers(client)
    resp = client.patch(
        "/company/profile", headers=headers,
        json={"linkedin_url": "https://www.linkedin.com/company/alpha-elsec"},
    )
    assert resp.status_code == 200
    assert resp.json()["linkedin_url"] == "https://www.linkedin.com/company/alpha-elsec"


def test_valid_linkedin_personal_profile_url_is_also_accepted(client):
    headers = _headers(client)
    resp = client.patch(
        "/company/profile", headers=headers,
        json={"linkedin_url": "https://linkedin.com/in/jane-doe"},
    )
    assert resp.status_code == 200


def test_non_linkedin_url_is_rejected(client):
    headers = _headers(client)
    resp = client.patch(
        "/company/profile", headers=headers,
        json={"linkedin_url": "https://facebook.com/alphaelsec"},
    )
    assert resp.status_code == 422


def test_plain_text_is_rejected(client):
    headers = _headers(client)
    resp = client.patch("/company/profile", headers=headers, json={"linkedin_url": "alpha-elsec"})
    assert resp.status_code == 422


def test_linkedin_url_is_never_marked_as_independently_verified(client):
    """
    This platform has no LinkedIn OAuth integration — the response
    must never claim verification it cannot back.
    """
    headers = _headers(client)
    resp = client.patch(
        "/company/profile", headers=headers,
        json={"linkedin_url": "https://www.linkedin.com/company/alpha-elsec"},
    ).json()
    assert "verified" not in resp
    assert "linkedin_verified" not in resp


def test_linkedin_url_appears_on_the_public_share_card(client):
    """
    A LinkedIn company page is public marketing material — unlike
    email/phone, it must be visible on the no-auth share card too.
    """
    headers = _headers(client)
    profile = client.patch(
        "/company/profile", headers=headers,
        json={"linkedin_url": "https://www.linkedin.com/company/alpha-elsec"},
    ).json()
    share_token = profile["share_token"]

    card = client.get(f"/public/companies/{share_token}")
    assert card.status_code == 200
    assert card.json()["linkedin_url"] == "https://www.linkedin.com/company/alpha-elsec"
    # And still never a contact channel on the public card.
    assert "contact_email" not in card.json()
    assert "gst_number" not in card.json()


def test_linkedin_url_appears_on_the_registered_vetting_profile(client):
    headers = _headers(client)
    profile = client.patch(
        "/company/profile", headers=headers,
        json={"linkedin_url": "https://www.linkedin.com/company/alpha-elsec"},
    ).json()

    # A second, independently registered tenant views the first one's
    # profile — the "register, then see the full picture" flow.
    other_headers = _headers(client)
    other_me = client.get("/auth/me", headers=other_headers).json()
    first_me = client.get("/auth/me", headers=headers).json()

    vetting = client.get(f"/companies/{first_me['tenant_id']}/profile", headers=other_headers)
    assert vetting.status_code == 200
    assert vetting.json()["linkedin_url"] == "https://www.linkedin.com/company/alpha-elsec"
