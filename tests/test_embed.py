import hashlib

from secours_tech import embed
from secours_tech.bench import hybrid, rrf, vector
from secours_tech.collect import Entry
from secours_tech.embed import MistralEmbedder, as_vector, embed_corpus, missing_inputs
from secours_tech.extract import Page
from secours_tech.load import SourceDocument, store_document


class FakeEmbedder:
    """Two dimensions: how much a text is about gas, how much about silos."""

    name = "fake"

    def __init__(self):
        self.sent = 0

    @staticmethod
    def _vector(text):
        text = text.lower()
        return [float("gaz" in text) + 0.01, float("silo" in text) + 0.01]

    def passages(self, texts):
        self.sent += len(texts)
        return [self._vector(t) for t in texts]

    def query(self, text):
        return self._vector(text)


def _guide(conn, texts=("Le silo stocke du grain.", "Couper le gaz avant de ventiler.")):
    pages = tuple(Page(i, t, "text_layer", 30, 0, 0, 1.0) for i, t in enumerate(texts, start=1))
    store_document(
        conn,
        SourceDocument("GDO-test.pdf", "a" * 64, list(texts), pages),
        Entry("GDO", "Test", "https://example.org/GDO-test.pdf", "a" * 64),
    )


def test_the_input_hash_is_the_context_then_the_text(conn):
    _guide(conn)
    context, text, sha = conn.execute(
        "SELECT context, text, input_sha256 FROM chunks ORDER BY page LIMIT 1"
    ).fetchone()
    expected = f"{context}\n{text}" if context else text
    assert sha == hashlib.sha256(expected.encode()).hexdigest()


def test_only_missing_inputs_are_embedded_and_rebuilt_chunks_keep_their_vectors(conn):
    _guide(conn)
    fake = FakeEmbedder()
    assert embed_corpus(conn, fake) == 2
    assert embed_corpus(conn, fake) == 0  # nothing new: no call
    conn.execute("DELETE FROM documents")  # chunks go with their document
    _guide(conn)  # the same texts loaded again: new chunk ids, same inputs
    assert missing_inputs(conn, "fake") == []
    assert fake.sent == 2


def test_vector_and_hybrid_search_find_the_page_about_the_question(conn):
    _guide(conn)
    fake = FakeEmbedder()
    embed_corpus(conn, fake)
    assert vector(conn, fake)("Que faire en présence de gaz ?")[0] == ("GDO-test.pdf", 2)
    assert hybrid(conn, fake)("Le gaz est-il coupé ?")[0] == ("GDO-test.pdf", 2)


def test_rrf_rewards_chunks_found_by_both_rankings():
    text, vectors = [(1, "a", 1), (2, "a", 2)], [(2, "a", 2), (3, "a", 3)]
    assert [row[0] for row in rrf(text, vectors)] == [2, 1, 3]


def test_mistral_embeddings_come_back_in_input_order(monkeypatch):
    def fake_post(path, payload, key):
        assert (path, payload["model"]) == ("embeddings", "mistral-embed-2312")
        return {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}

    monkeypatch.setattr(embed, "post", fake_post)
    assert MistralEmbedder("k").passages(["un", "deux"]) == [[1.0], [2.0]]


def test_vectors_are_written_in_pgvector_text_form():
    assert as_vector([1, 0.5]) == "[1.0,0.5]"


def test_the_bench_refuses_vector_scores_when_chunks_lack_a_vector(conn, tmp_path, monkeypatch):
    import os

    import pytest

    from secours_tech.bench import main

    _guide(conn)
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "Q01.toml").write_text(
        'id = "Q01"\ncategory = "simple"\nsplit = "dev"\nquestion = "Gaz ?"\n'
        'expected_answer = "Couper."\nserious_fault_if = "rien"\n\n[[excerpts]]\n'
        'document = "GDO-test.pdf"\npage = 2\ntext = "Couper le gaz"\n',
        encoding="utf-8",
    )
    monkeypatch.setitem(embed.EMBEDDERS, "mistral", FakeEmbedder)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    argv = ["--cases", str(cases), "--fingerprint", str(tmp_path / "none"), "run", "--retriever"]
    with pytest.raises(SystemExit, match="have no fake vector"):
        main([*argv, "vector"])
    embed_corpus(conn, FakeEmbedder())
    main([*argv, "hybrid"])  # every chunk has its vector: the run goes through
