import pytest

from secours_tech.ingest import chunk_text


def test_short_text_is_one_chunk():
    assert chunk_text("hello   world\n", size=50, overlap=10) == ["hello world"]


def test_empty_text_gives_no_chunk():
    assert chunk_text("  \n ") == []


def test_chunks_overlap():
    chunks = chunk_text("abcdefghij", size=4, overlap=2)
    assert chunks == ["abcd", "cdef", "efgh", "ghij", "ij"]


def test_invalid_overlap_is_rejected():
    with pytest.raises(ValueError):
        chunk_text("abc", size=4, overlap=4)
