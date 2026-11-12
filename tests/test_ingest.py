import logging

import pytest

from secours_tech.ingest import chunk_text, pages_to_chunks, strip_repeated_lines


def test_short_text_is_one_chunk():
    assert chunk_text("hello   world\n", size=50) == ["hello world"]


def test_empty_text_gives_no_chunk():
    assert chunk_text("  \n ") == []


def test_chunks_cut_on_sentence_boundaries_with_overlap():
    text = "One two. Three four. Five six. Seven eight."
    chunks = chunk_text(text, size=22, overlap=1)
    assert chunks == ["One two. Three four.", "Three four. Five six.", "Five six. Seven eight."]


def test_every_sentence_is_kept():
    text = " ".join(f"Sentence number {i}." for i in range(30))
    joined = " ".join(chunk_text(text, size=60, overlap=0))
    assert all(f"Sentence number {i}." in joined for i in range(30))


def test_overlong_sentence_is_split_hard_and_respects_size():
    chunks = chunk_text("a" * 25, size=10)
    assert chunks == ["a" * 10, "a" * 10, "a" * 5]


def test_invalid_size_is_rejected():
    with pytest.raises(ValueError):
        chunk_text("abc", size=0)


def test_repeated_header_and_page_number_are_removed():
    bodies = ["Alpha rules.", "Beta rules.", "Gamma rules.", "Delta rules.", "Eps rules.", "Zeta."]
    pages = [f"GUIDE DGSCGC 2019\n{b}\nPage {i}" for i, b in enumerate(bodies, start=1)]
    cleaned = strip_repeated_lines(pages)
    assert cleaned[0] == "Alpha rules."
    assert "GUIDE" not in "".join(cleaned)


def test_pages_keep_their_page_number():
    chunks = pages_to_chunks("doc.pdf", ["First page text.", "Second page text."])
    assert [(c.page, c.text) for c in chunks] == [(1, "First page text."), (2, "Second page text.")]


def test_empty_page_is_reported(caplog):
    with caplog.at_level(logging.WARNING):
        chunks = pages_to_chunks("scan.pdf", ["Some text.", "   "])
    assert [c.page for c in chunks] == [1]
    assert "scan.pdf page 2 has no extractable text" in caplog.text


def test_header_is_removed_even_when_most_pages_are_empty():
    bodies = ["Ventiler.", "Couper le gaz.", "Baliser la zone.", "Éclairer."]
    pages = [""] * 8 + [f"GDO GAZ 2019\n{body}" for body in bodies]
    cleaned = strip_repeated_lines(pages)
    assert "GDO GAZ" not in "".join(cleaned)
    assert cleaned[-1] == "Éclairer."


def test_ocr_footer_is_removed_despite_logo_noise():
    footer = "DIRECTION GÉNÉRALE DE LA SÉCURITÉ CIVILE ET DE LA GESTION DES CRISES"
    noise = ["@&", "À\\ /", "", "@æ)"]
    bodies = ["Ventiler le local.", "Couper le gaz.", "Baliser la zone.", "Éclairer."]
    pages = [f"{body}\n{n} {footer}".strip() for body, n in zip(bodies, noise, strict=True)]
    assert strip_repeated_lines(pages) == bodies


def test_bare_page_numbers_are_removed():
    bodies = ["Ventiler le local.", "Couper le gaz.", "Baliser la zone.", "Éclairer."]
    pages = [f"{body}\n{i}" for i, body in enumerate(bodies, start=12)]
    assert strip_repeated_lines(pages) == bodies


WORDS = ["ventiler", "couper", "baliser", "éclairer", "alerter", "protéger"]


def test_table_values_alone_on_their_line_are_kept():
    # page numbers make a bare number a frequent shape; a table value is not a page number
    pages = [
        f"Distance pour {w} (m)\n{10 * i + 40}\nDébit pour {w} (l/min)\n500\nFin {w}.\n{i}"
        for i, w in enumerate(WORDS, start=1)
    ]
    cleaned = strip_repeated_lines(pages)
    assert cleaned[0].splitlines() == [
        "Distance pour ventiler (m)",
        "50",
        "Débit pour ventiler (l/min)",
        "500",
        "Fin ventiler.",
    ]


def test_value_at_the_bottom_that_is_not_the_page_number_is_kept():
    pages = [f"Tableau {w}.\nDébit {w}\n{100 + 7 * i}\n{i + 3}" for i, w in enumerate(WORDS)]
    cleaned = strip_repeated_lines(pages)
    assert [page.splitlines()[-1] for page in cleaned] == [str(100 + 7 * i) for i in range(6)]


def test_flowchart_answers_are_kept_and_its_header_removed():
    pages = [
        f"LOGIGRAMME PSE\nLa victime {w} ?\noui\nAction {w}.\nnon\nAlerter {w}.\noui\n{n}"
        for n, w in enumerate(["respire", "parle", "saigne", "bouge", "voit", "entend"], start=1)
    ]
    cleaned = strip_repeated_lines(pages)
    assert cleaned[0].splitlines() == [
        "La victime respire ?",
        "oui",
        "Action respire.",
        "non",
        "Alerter respire.",
        "oui",
    ]


def test_page_numbers_follow_the_given_page_numbers():
    # OCR pages are a subset of the guide: their printed numbers follow their own page numbers
    numbers = [4, 9, 10, 15]
    pages = [f"Il faut {w}.\n{n - 2}" for w, n in zip(WORDS, numbers, strict=False)]
    cleaned = strip_repeated_lines(pages, numbers=numbers)
    assert cleaned == [f"Il faut {w}." for w in WORDS[:4]]
