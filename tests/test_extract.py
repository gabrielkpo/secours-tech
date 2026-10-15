from pathlib import Path

import pytest

from secours_tech.extract import extract_pages, route, text_quality

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
