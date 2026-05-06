from services import language_service


def test_detects_common_spanish_likely_surname():
    assert language_service.language_hint_for_name("Maria Garcia") == "spanish_likely"


def test_detects_multi_word_spanish_likely_surname():
    assert language_service.language_hint_for_name("Jose De La Cruz") == "spanish_likely"


def test_detects_accented_surname_after_normalization():
    assert language_service.language_hint_for_name("Ana Núñez") == "spanish_likely"


def test_does_not_match_first_name_only():
    assert language_service.language_hint_for_name("Garcia Smith") is None
