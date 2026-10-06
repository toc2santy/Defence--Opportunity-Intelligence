"""Pure-logic tests for app/taxonomy_learning.py (no DB, no HTTP)."""

from app.taxonomy_learning import (
    Doc, clean_title, dedupe_docs, mapping_table_for_source, mine_codes, mine_keywords, title_ngrams,
)

CAP = "cap-aero"
OTHER = "cap-other"


def _docs(titles, code="336413", source="SAM.gov", buyers=None):
    out = []
    for i, t in enumerate(titles):
        d = Doc(title=t, buyer=(buyers[i] if buyers else f"buyer{i}"), code=code, source=source)
        d.ngrams = title_ngrams(t)
        out.append(d)
    return out


def test_ted_prefix_is_stripped_so_country_and_cpv_label_are_not_mined():
    t = "Germany – Fire engines – Lieferung eines Hilfeleistungsfahrzeuges"
    assert clean_title(t) == "Lieferung eines Hilfeleistungsfahrzeuges"
    grams = title_ngrams(t)
    assert not any("germany" in g or "engines" in g for g in grams)


def test_phrases_never_start_or_end_with_a_function_word():
    grams = title_ngrams("Protective and safety clothing for the army")
    assert "safety clothing" in grams
    assert "protective and" not in grams
    assert "and safety clothing" not in grams
    assert not any(g.endswith(" for") or g.startswith("the ") for g in grams)


def test_digits_and_short_single_words_are_never_candidates():
    grams = title_ngrams("NSN 2840012168046 UAV pump 12345")
    assert "uav" not in grams          # 3 chars: the 'UAV KUMBHIGRAM' lesson
    assert not any(any(ch.isdigit() for ch in g) for g in grams)


def test_cyrillic_and_accented_titles_are_mined_in_their_own_language():
    assert "тренувальні мішені" in title_ngrams("Тренувальні мішені")
    assert "chaleco balistico" in title_ngrams("Chaleco BALÍSTICO")


def test_recurring_identical_titles_count_once():
    docs = _docs(["Blade set, fan", "BLADE SET  FAN", "Blade set fan!"])
    assert len(dedupe_docs(docs)) == 1


def _corpus(n_target=12, word="turbineblade"):
    target = _docs([f"{word} housing {chr(97 + i) * 4}" for i in range(n_target)])
    noise = _docs([f"office furniture lot {chr(97 + i) * 4}" for i in range(40)], code="999999", source="X")
    return target + noise


def test_high_precision_phrase_is_learned_automatically_and_generic_noise_is_not():
    docs = dedupe_docs(_corpus())
    got = mine_keywords(docs, {"336413": CAP}, {CAP: set()}, set())
    learned = [s for s in got if "turbineblade" in s.keyword]
    # the longer phrase wins over its bare word when support is the same
    assert [s.keyword for s in learned] == ["turbineblade housing"]
    assert learned[0].tier == "auto" and learned[0].precision == 100.0
    assert "furniture" not in " ".join(s.keyword for s in got)   # outside the capability's codes


def test_code_shared_by_two_capabilities_is_not_evidence_for_either():
    docs = dedupe_docs(_corpus())
    assert mine_keywords(docs, {"336413": None}, {}, set()) == []


def test_existing_and_rejected_keywords_are_never_proposed_again():
    docs = dedupe_docs(_corpus())
    assert not [s for s in mine_keywords(docs, {"336413": CAP}, {CAP: {"turbineblade"}}, set()) if s.keyword == "turbineblade"]
    assert not [s for s in mine_keywords(docs, {"336413": CAP}, {}, {(CAP, "turbineblade")}) if s.keyword == "turbineblade"]


def test_truncated_stub_of_a_longer_word_is_not_learned():
    docs = _docs([f"BLADE SET AIRCR {chr(97 + i) * 4}" for i in range(6)] + [f"aircraft blade {chr(97 + i) * 4}" for i in range(6)])
    got = {s.keyword for s in mine_keywords(dedupe_docs(docs), {"336413": CAP}, {}, set())}
    assert not any("aircr" == g.split()[-1] or g.startswith("aircr ") for g in got)


def test_single_buyer_phrase_is_not_learned_at_all():
    docs = _docs([f"zorbulaxium gasket {chr(97 + i) * 4}" for i in range(12)], buyers=["one-office"] * 12)
    assert mine_keywords(dedupe_docs(docs), {"336413": CAP}, {}, set()) == []


def test_few_buyers_on_one_source_is_pending_not_auto():
    docs = _docs([f"zorbulaxium gasket {chr(97 + i) * 4}" for i in range(12)], buyers=["a", "b", "c"] * 4)
    docs += _docs([f"office furniture lot {chr(97 + i) * 4}" for i in range(40)], code="999999", source="X")
    s = [x for x in mine_keywords(dedupe_docs(docs), {"336413": CAP}, {}, set()) if "zorbulaxium" in x.keyword]
    assert s and s[0].tier == "pending"


KW_ROWS = [(CAP, "SENSORS.GENERAL", "Sensors", "Sensors", "surveillance system", 3)]


def test_unmapped_code_whose_titles_keep_scoring_is_suggested_but_mapped_code_is_not():
    docs = _docs([f"video surveillance system lot {chr(97 + i) * 4}" for i in range(8)], code="35120000", source="EU TED (x)")
    got = mine_codes(docs, set(), KW_ROWS, set())
    assert [(c.code, c.capability_id, c.hits) for c in got] == [("35120000", CAP, 8)]
    assert mine_codes(docs, {"35120000"}, KW_ROWS, set()) == []
    assert mine_codes(docs, set(), KW_ROWS, {(CAP, "35120000")}) == []


def test_code_with_mostly_unrelated_titles_is_not_suggested():
    docs = _docs([f"video surveillance system {chr(97 + i) * 4}" for i in range(4)] + [f"catering lot {chr(97 + i) * 4}" for i in range(20)], code="55555555")
    assert mine_codes(docs, set(), KW_ROWS, set()) == []


def test_approved_code_goes_into_the_mapping_table_that_matches_its_source():
    assert mapping_table_for_source("SAM.gov Contract Opportunities API")[0] == "taxonomy_naics_mapping"
    assert mapping_table_for_source("EU TED (Tenders Electronic Daily)")[0] == "taxonomy_cpv_mapping"
    assert mapping_table_for_source("AusTender (Australian Government)")[0] == "taxonomy_unspsc_mapping"
