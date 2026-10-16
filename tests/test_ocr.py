import shutil
from pathlib import Path

import pytest

from secours_tech.ocr import MODEL_DIR, fetch_model, ocr_page, text_from_tsv
from secours_tech.ocr_bench import GuideScore, edit_distance, normalize, numbers, score_page

TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t96.0\tDistance\n"
    "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t90.0\tde\n"
    "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t84.0\t10\n"
    "5\t1\t1\t1\t2\t2\t0\t0\t10\t10\t-1\t \n"
    "5\t1\t1\t1\t2\t3\t0\t0\t10\t10\t70.0\tmètres\n"
)


def test_tsv_is_rebuilt_line_by_line_with_mean_confidence():
    result = text_from_tsv(TSV)
    assert result.text == "Distance de\n10 mètres"
    assert result.confidence == 85.0


def test_normalize_ignores_layout_and_quote_styles():
    assert normalize("l’intervention\n  « COS »") == 'l\'intervention"COS"'


def test_edit_distance():
    assert edit_distance("10 mètres", "16 mètres") == 1
    assert edit_distance("", "abc") == 3


def test_numbers_keep_decimals_and_repeats():
    assert numbers("1,25 m puis 6 m et 6 m") == {"1,25": 1, "6": 2}


def test_a_misread_number_fails_the_guide_even_with_a_tiny_error_rate():
    reference = "La liaison mesure 6 mètres. " * 20
    page = score_page(1, reference, reference.replace("6 mètres", "8 mètres", 1))
    guide = GuideScore("GTO-test.pdf", [page])
    assert guide.cer < 0.01
    assert page.missing_numbers == ["6"] and page.extra_numbers == ["8"]
    assert not guide.passed


def test_clean_ocr_passes():
    reference = "Couper l'alimentation à 10 mètres du transformateur."
    guide = GuideScore("GDO-test.pdf", [score_page(3, reference, reference.replace("'", "’"))])
    assert guide.passed


def test_a_model_with_the_wrong_fingerprint_is_refused(tmp_path: Path):
    (tmp_path / "fra.traineddata").write_bytes(b"not the pinned model")
    with pytest.raises(RuntimeError, match="expected"):
        fetch_model(tmp_path)
    assert not (tmp_path / "fra.traineddata").exists()


@pytest.mark.skipif(
    shutil.which("tesseract") is None or not (MODEL_DIR / "fra.traineddata").exists(),
    reason="tesseract or the French model is not installed",
)
def test_a_rendered_page_is_read_back(make_pdf):
    pdf = make_pdf(["Distance de securite 10 metres autour du transformateur"])
    result = ocr_page(pdf, 1)
    assert "10" in result.text
    assert "transformateur" in result.text
    assert result.confidence > 50


def test_normalize_ignores_list_bullets_read_by_the_ocr():
    ocr = "e ne pas dégrader la protection ;\no couper le gaz ;\n- baliser."
    assert normalize(ocr) == normalize("ne pas dégrader la protection ;\ncouper le gaz ;\nbaliser.")
    assert normalize("à proposer un dispositif") == "à proposer un dispositif"


def test_normalize_ignores_french_typographic_spaces_dashes_and_ellipsis():
    reference = "« cône d’effluves » ; voir annexe A – page 3…"
    ocr = '"cône d\'effluves"; voir annexe A - page 3...'
    assert normalize(reference) == normalize(ocr)


def test_normalize_drops_table_of_contents_leaders_and_joins_cut_words():
    assert normalize("5.1. Les éléments à recueillir.......... 37") == normalize(
        "5.1. Les éléments à recueillir 37"
    )
    assert normalize("les sapeurs-\npompiers") == "les sapeurs-pompiers"
    assert normalize("✓ la police nationale") == "la police nationale"


def test_transcriptions_skip_files_that_are_not_a_page(tmp_path: Path):
    from secours_tech.ocr_bench import transcriptions

    guide = tmp_path / "GTO-test"
    guide.mkdir()
    for name in ("p12.txt", "img-p12.txt", "photo-notes.txt", "p3.png"):
        (guide / name).write_text("x")
    assert transcriptions(tmp_path) == [(guide / "p12.txt", "GTO-test", 12)]
