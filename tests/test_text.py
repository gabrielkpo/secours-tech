from secours_tech.text import normalize


def test_invisible_characters_do_not_hide_a_difference_or_make_one():
    assert normalize("cou\u00adper le g\u200baz") == "couper le gaz"
    assert normalize("\ufeffcouper") == "couper"


def test_quotes_dashes_and_french_spaces_compare_equal():
    assert normalize("« COS » – l’accès ; interdit") == normalize('"COS" - l\'accès; interdit')
    assert normalize("50\u00a0m\u202f!") == normalize("50 m!")


def test_case_accents_and_words_still_count():
    assert normalize("Électricité") != normalize("electricite")
    assert normalize("couper le gaz") != normalize("couper le gaz naturel")
