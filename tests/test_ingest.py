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


def test_lines_without_words_are_never_taken_for_a_footer():
    pages = [f"Étape {i}.\n1 2 3" for i in range(4)]
    assert all("1 2 3" in page for page in strip_repeated_lines(pages))
