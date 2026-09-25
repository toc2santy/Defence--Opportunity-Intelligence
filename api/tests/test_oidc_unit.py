"""
Pure unit tests for app/oidc.py's _issuer_matches — no DB, no network,
no FastAPI. Covers the real bug found live (2026-09) testing "Continue
with Microsoft" against a real account: Microsoft's multi-tenant
"common" endpoint's own discovery document literally contains the
placeholder string "{tenantid}" in its issuer field, which a real
id_token's iss claim replaces with the real tenant GUID that signed
it — an exact string match (correct for Google, wrong for Microsoft)
can never pass this by design, not by bug.
"""

from app.oidc import _issuer_matches

MICROSOFT_COMMON_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"


def test_exact_match_still_works_for_a_single_tenant_provider_like_google():
    assert _issuer_matches("https://accounts.google.com", "https://accounts.google.com") is True


def test_exact_match_rejects_a_different_issuer():
    assert _issuer_matches("https://evil.example.com", "https://accounts.google.com") is False


def test_microsoft_template_matches_a_real_tenant_guid():
    real_iss = "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/v2.0"
    assert _issuer_matches(real_iss, MICROSOFT_COMMON_TEMPLATE) is True


def test_microsoft_template_rejects_the_unsubstituted_literal_common():
    # The exact bug: before the fix, the code compared against this
    # literal string, which a real token's iss claim never equals.
    assert _issuer_matches("https://login.microsoftonline.com/common/v2.0", MICROSOFT_COMMON_TEMPLATE) is False


def test_microsoft_template_rejects_a_non_guid_segment():
    assert _issuer_matches("https://login.microsoftonline.com/not-a-real-guid/v2.0", MICROSOFT_COMMON_TEMPLATE) is False


def test_microsoft_template_rejects_a_completely_different_host():
    real_guid = "72f988bf-86f1-41af-91ab-2d7cd011db47"
    assert _issuer_matches(f"https://evil.example.com/{real_guid}/v2.0", MICROSOFT_COMMON_TEMPLATE) is False


def test_microsoft_template_rejects_extra_path_segments():
    real_guid = "72f988bf-86f1-41af-91ab-2d7cd011db47"
    assert _issuer_matches(f"https://login.microsoftonline.com/{real_guid}/v2.0/extra", MICROSOFT_COMMON_TEMPLATE) is False
