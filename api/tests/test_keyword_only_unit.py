"""Pure / in-process checks for keyword-only matching (no HTTP)."""

from app.programme_matching import _like_pattern, keyword_only_enabled


def test_flag_is_off_by_default_and_read_on_every_call(monkeypatch):
    monkeypatch.delenv("KEYWORD_ONLY_MATCHING", raising=False)
    assert keyword_only_enabled() is False
    monkeypatch.setenv("KEYWORD_ONLY_MATCHING", "true")
    assert keyword_only_enabled() is True
    monkeypatch.setenv("KEYWORD_ONLY_MATCHING", "TRUE ")
    assert keyword_only_enabled() is True
    monkeypatch.setenv("KEYWORD_ONLY_MATCHING", "yes")
    assert keyword_only_enabled() is False, "only the literal 'true' enables it"


def test_like_prefilter_turns_accents_into_wildcards_so_it_can_only_over_select():
    assert _like_pattern("munición") == "%munici_n%"
    assert _like_pattern("Spare Parts") == "%spare parts%"
    assert _like_pattern("100%") == "%100\\%%"
    # a non-ASCII keyword must still match both accent spellings in SQL LIKE terms
    import re
    rx = re.compile("^" + re.escape(_like_pattern("munición")[1:-1]).replace("_", ".") + "$")
    assert rx.match("munición") and rx.match("municion")
