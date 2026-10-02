"""Pure unit tests for app/signup_policy.py — no DB, no network."""

from app.signup_policy import is_allowed, parse_allowlist


def test_star_means_open_signup():
    assert is_allowed("anyone@anywhere.com", parse_allowlist("*")) is True


def test_unset_or_blank_defaults_to_open_for_dev():
    assert parse_allowlist("") == ["*"]
    assert parse_allowlist(None) == ["*"]


def test_exact_email_match_is_case_and_whitespace_insensitive():
    allow = parse_allowlist(" Boss@Syas.IN , other@example.com ")
    assert is_allowed("boss@syas.in", allow)
    assert is_allowed("  OTHER@example.com", allow)


def test_domain_entry_allows_everyone_at_that_domain_only():
    allow = parse_allowlist("@alpha-elsec.com")
    assert is_allowed("santosh.kumar@alpha-elsec.com", allow)
    assert not is_allowed("santosh.kumar@alpha-elsec.com.evil.io", allow)
    assert not is_allowed("x@notalpha-elsec.com", allow)


def test_a_listed_domain_does_not_match_a_lookalike_local_part():
    # "@a.com" must not be satisfied by a local part containing it.
    allow = parse_allowlist("@a.com")
    assert not is_allowed("x@a.com@evil.com", allow)


def test_everyone_else_is_rejected_when_a_list_is_set():
    allow = parse_allowlist("boss@syas.in")
    assert not is_allowed("stranger@example.com", allow)
    assert not is_allowed("", allow)
    assert not is_allowed("not-an-email", allow)
