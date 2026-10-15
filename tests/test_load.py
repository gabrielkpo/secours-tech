import os

import psycopg
import pytest

from secours_tech.db import migrate
from secours_tech.load import SourceDocument, family_of, is_loaded, store_document, title_of


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
