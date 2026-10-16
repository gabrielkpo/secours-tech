from pathlib import Path

import pytest

from secours_tech.extract import (
    control_chars,
    extract_pages,
    french_share,
    route,
    text_quality,
    word_overlap,
)

SENTENCE = "Couper l'alimentation électrique avant toute intervention sur le transformateur."


def test_clean_text_has_full_quality():
    assert text_quality(SENTENCE) == 1.0


def test_garbled_text_layer_has_low_quality():
    assert text_quality("���\x01\x02 ab") < 0.6


@pytest.mark.parametrize(
    ("letters", "text_masks", "images", "quality", "expected"),
    [
        (800, 0, 2, 1.0, "text_layer"),  # ordinary page with a photo
        (120, 25, 26, 1.0, "ocr"),  # headings as text, body lines drawn as images
        (0, 30, 30, 1.0, "ocr"),  # every line drawn as an image
        (10, 0, 1, 1.0, "ocr"),  # full-page image or scan
        (900, 0, 0, 0.3, "ocr"),  # garbled text layer
        (20, 0, 0, 1.0, "text_layer"),  # divider page with only a title
        (0, 0, 0, 1.0, "blank"),  # verso
        (400, 2, 2, 1.0, "text_layer"),  # one or two masks are a logo, not text
    ],
)
def test_route(letters, text_masks, images, quality, expected):
    assert route(letters, text_masks, images, quality) == expected


def test_pages_are_read_with_their_route(make_pdf):
    pages = extract_pages(make_pdf([SENTENCE, ""]))
    assert [(p.number, p.route) for p in pages] == [(1, "text_layer"), (2, "blank")]
    assert "transformateur" in pages[0].text
    assert pages[1].text == ""


def test_relative_path_starting_with_a_dash_is_read_as_a_file(make_pdf, monkeypatch):
    pdf = make_pdf([SENTENCE], "-q.pdf")
    monkeypatch.chdir(pdf.parent)
    assert extract_pages(Path("-q.pdf"))[0].route == "text_layer"


FRENCH = (
    "Le chef d'agrès fait couper le courant avant toute action sur le transformateur et "
    "fait établir une lance en jet diffusé pour protéger les intervenants de la chaleur."
)
GARBLED = "EFGHIJKLJHMNFGLMIM LOPIJGOJQRFGSFG TFRUSFTMVMSFGGWKOX " * 8


def test_french_text_has_function_words_and_garbage_has_none():
    assert french_share(FRENCH) > 0.2
    assert french_share(GARBLED) == 0.0
    assert french_share("Le chef d'agrès") is None  # a title: too few words to judge


def test_word_overlap_ignores_reading_order():
    assert word_overlap(FRENCH, " ".join(reversed(FRENCH.split()))) == 1.0
    assert word_overlap("abcdef ghijkl mnopqr", FRENCH) == 0.0


def test_split_accents_are_read_whole_and_composed(make_pdf, monkeypatch):
    from secours_tech import extract

    split = "des poussie\u0300 res fines peuvent e\u0302 tre e\u0301 vite\u0301 es"
    whole = "des poussie\u0300res fines peuvent e\u0302tre e\u0301vite\u0301es"
    calls = []

    def fake(path, *options):
        calls.append(options)
        return [whole] if options else [split]

    monkeypatch.setattr(extract, "_pdftotext", fake)
    assert extract._read_text(make_pdf([SENTENCE])) == ["des poussières fines peuvent être évitées"]
    assert calls == [(), ("-raw",)]


def test_control_characters_mark_a_font_without_tounicode():
    assert control_chars('\x15\x16\x17\x18\x19\x1a\x1b ! "#$') == 7
    assert control_chars(FRENCH + "\n\t\f") == 0
