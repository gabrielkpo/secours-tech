import os

import psycopg
import pytest

from secours_tech.collect import Entry
from secours_tech.db import migrate
from secours_tech.extract import Page
from secours_tech.load import (
    SourceDocument,
    family_of,
    is_loaded,
    read_source,
    store_document,
    sync_with_manifest,
    title_of,
)


@pytest.mark.parametrize(
    ("filename", "family"),
    [
        ("GDO-Interventions-Silos.pdf", "GDO"),
        ("GDO_OPERATIONS_MILIEU_ROUTIER_2025_DGSCGC.pdf", "GDO"),
        ("GDO-SSUAP-V1.1.pdf", "SSUAP"),
        ("GTO-ventilation-operationnelle_VF_2019.pdf", "GTO"),
        ("2024 PSE.pdf", "PSE"),
        ("Recueil PSE.pdf", "PSE"),
    ],
)
def test_family_is_read_from_the_file_name(filename, family):
    assert family_of(filename) == family


def test_unknown_family_is_an_error():
    with pytest.raises(ValueError):
        family_of("notes.pdf")


def test_title_is_readable():
    assert title_of("GTO_FDF_1e-edition_2021.pdf") == "GTO FDF 1e edition 2021"


# --- against a real Postgres -------------------------------------------------------------

TEST_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def conn():
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        name = conn.execute("SELECT current_database()").fetchone()[0]
        # the fixture wipes the schema: refuse anything that is not a test database
        assert name.endswith("_test"), f"refusing to wipe {name!r}"
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        migrate(conn)
        yield conn


DOC = SourceDocument(
    filename="GDO-Interventions-Silos.pdf",
    sha256="a" * 64,
    page_texts=["Silo safety distance is 50 m.", "   ", "Ventilate before entering."],
)


def test_migrations_apply_once(conn):
    assert migrate(conn) == []


def test_document_and_chunks_are_stored_with_their_page(conn):
    assert store_document(conn, DOC) == 2
    doc = conn.execute(
        "SELECT family, source, page_count, textless_page_count FROM documents"
    ).fetchone()
    assert doc == ("GDO", "local", 3, 1)  # page 2 gives no chunk
    rows = conn.execute("SELECT page, position, text FROM chunks ORDER BY position").fetchall()
    assert rows == [
        (1, 0, "Silo safety distance is 50 m."),
        (3, 1, "Ventilate before entering."),
    ]


def test_loading_the_same_file_twice_changes_nothing(conn):
    store_document(conn, DOC)
    assert is_loaded(conn, DOC.sha256)
    assert store_document(conn, DOC) is None
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2


def test_a_recompressed_copy_with_the_same_text_is_not_loaded_twice(conn):
    store_document(conn, DOC)
    copy = SourceDocument("GDO-Interventions-Silos-compressed.pdf", "d" * 64, DOC.page_texts)
    assert store_document(conn, copy) is None
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_full_text_search_finds_french_word_forms(conn):
    store_document(
        conn,
        SourceDocument("GTO-test.pdf", "b" * 64, ["Ventiler le local avant d'entrer."]),
    )
    hit = conn.execute(
        "SELECT count(*) FROM chunks WHERE tsv @@ websearch_to_tsquery('fr', 'ventilation')"
    ).fetchone()[0]
    assert hit == 1


def test_full_text_search_ignores_accents(conn):
    store_document(
        conn,
        SourceDocument("GDO-test.pdf", "c" * 64, ["Couper l'électricité avant d'intervenir."]),
    )
    hit = conn.execute(
        "SELECT count(*) FROM chunks WHERE tsv @@ websearch_to_tsquery('fr', 'electricite')"
    ).fetchone()[0]
    assert hit == 1


def test_chunks_record_how_their_text_was_extracted(conn):
    store_document(conn, DOC)
    assert conn.execute("SELECT DISTINCT extraction FROM chunks").fetchall() == [("text_layer",)]


def test_two_different_fully_scanned_guides_are_both_loaded(conn):
    scan_a = SourceDocument("GTO-scan-a.pdf", "e" * 64, ["", "  "])
    scan_b = SourceDocument("GTO-scan-b.pdf", "f" * 64, ["", "  "])
    assert store_document(conn, scan_a) == 0
    assert store_document(conn, scan_b) == 0
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 2


def test_official_guide_takes_its_family_title_and_url_from_the_manifest(conn):
    entry = Entry("GDO", "Interventions dans les silos", "https://example.org/silos.pdf", "a" * 64)
    store_document(conn, DOC, entry)
    row = conn.execute("SELECT family, title, source, url, version FROM documents").fetchone()
    assert row == ("GDO", "Interventions dans les silos", "web", "https://example.org/silos.pdf", 1)


def test_a_new_edition_at_the_same_url_retires_the_previous_one(conn):
    url = "https://example.org/silos.pdf"
    store_document(conn, DOC, Entry("GDO", "Silos", url, DOC.sha256))
    edition_2 = SourceDocument(DOC.filename, "9" * 64, ["Silo safety distance is 60 m."])
    assert store_document(conn, edition_2, Entry("GDO", "Silos", url, edition_2.sha256)) == 1
    rows = conn.execute(
        "SELECT version, retired_at IS NOT NULL FROM documents ORDER BY version"
    ).fetchall()
    assert rows == [(1, True), (2, False)]


def test_the_manifest_decides_which_guides_are_current(conn):
    silos = Entry("GDO", "Silos", "https://example.org/silos.pdf", DOC.sha256)
    store_document(conn, DOC, silos)
    assert sync_with_manifest(conn, []) == (1, 0)  # dropped from the manifest: retired
    assert sync_with_manifest(conn, [silos]) == (0, 1)  # put back: restored
    assert conn.execute("SELECT retired_at IS NULL FROM documents").fetchone() == (True,)


def test_sync_keeps_only_the_listed_edition_current(conn):
    url = "https://example.org/silos.pdf"
    store_document(conn, DOC, Entry("GDO", "Silos", url, DOC.sha256))
    edition_2 = SourceDocument(DOC.filename, "9" * 64, ["Silo safety distance is 60 m."])
    store_document(conn, edition_2, Entry("GDO", "Silos", url, edition_2.sha256))
    assert sync_with_manifest(conn, [Entry("GDO", "Silos", url, edition_2.sha256)]) == (0, 0)
    current = conn.execute("SELECT version FROM documents WHERE retired_at IS NULL").fetchall()
    assert current == [(2,)]


def test_pages_are_stored_with_their_route(conn, make_pdf):
    pdf = make_pdf(["Ventiler le local avant d'entrer dans le silo.", ""], "GDO-Silos.pdf")
    store_document(conn, read_source(pdf, pdf.read_bytes()))
    rows = conn.execute("SELECT page, route FROM pages ORDER BY page").fetchall()
    assert rows == [(1, "text_layer"), (2, "blank")]


def test_ocr_sample_only_draws_pages_with_text_drawn_as_images(conn):
    from secours_tech.ocr_bench import sample

    pages = (
        Page(1, "", "ocr", 120, 25, 26, 1.0),  # lines of text drawn as images
        Page(2, "", "ocr", 5, 0, 1, 1.0),  # photo page
    )
    store_document(conn, SourceDocument("GTO-test.pdf", "a" * 64, ["", ""], pages))
    assert sample(conn, per_guide=5, seed=1) == {"GTO-test.pdf": [1]}


def test_ocr_bench_reads_ocr_text_without_its_running_footer(conn):
    from secours_tech.ocr_bench import ocr_pages

    pages = tuple(Page(i, "", "ocr", 120, 25, 26, 1.0) for i in range(1, 5))
    store_document(conn, SourceDocument("GTO-test.pdf", "a" * 64, [""] * 4, pages))
    footer = "@& DIRECTION GÉNÉRALE DE LA SÉCURITÉ CIVILE ET DE LA GESTION DES CRISES"
    bodies = {1: "Ventiler.", 2: "Couper le gaz.", 3: "Baliser la zone.", 4: "Éclairer."}
    for page, body in bodies.items():
        conn.execute("UPDATE pages SET ocr_text = %s WHERE page = %s", (f"{body}\n{footer}", page))
    assert ocr_pages(conn, "GTO-test.pdf") == bodies


def test_ocr_sample_can_exclude_pages_already_transcribed(conn):
    from secours_tech.ocr_bench import sample

    pages = tuple(Page(i, "", "ocr", 120, 25, 26, 1.0) for i in range(1, 5))
    store_document(conn, SourceDocument("GTO-test.pdf", "a" * 64, [""] * 4, pages))
    drawn = sample(conn, per_guide=2, seed=1, exclude=frozenset({("GTO-test", 1), ("GTO-test", 2)}))
    assert drawn == {"GTO-test.pdf": [3, 4]}


def test_reextract_replaces_text_chunks_and_routes_but_keeps_ocr_text(conn):
    from secours_tech.load import reextract_document

    garbled = (Page(1, "EFGH IJKL", "text_layer", 8, 0, 0, 1.0), Page(2, "", "ocr", 0, 30, 30, 1.0))
    store_document(conn, SourceDocument("GDO-test.pdf", "a" * 64, ["EFGH IJKL", ""], garbled))
    (doc_id,) = conn.execute("SELECT id FROM documents").fetchone()
    conn.execute("UPDATE pages SET ocr_text = 'Ventiler le local.' WHERE page = 2")

    fixed = (Page(1, "", "ocr", 8, 0, 0, 1.0), Page(2, "", "ocr", 0, 30, 30, 1.0))
    assert (
        reextract_document(conn, doc_id, SourceDocument("GDO-test.pdf", "a" * 64, ["", ""], fixed))
        == 0
    )
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    routes = conn.execute("SELECT page, route, ocr_text FROM pages ORDER BY page").fetchall()
    assert routes == [(1, "ocr", None), (2, "ocr", "Ventiler le local.")]
