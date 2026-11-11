from secours_tech.bench import _fulltext_chunks, reranked
from secours_tech.extract import Page
from secours_tech.load import SourceDocument, store_document
from secours_tech.rerank import rerank


class SiloFirst:
    """Scores a passage by how often it mentions silos."""

    name = "fake"

    def scores(self, question, passages):
        return [passage.lower().count("silo") for passage in passages]


def test_rerank_orders_chunks_by_score_and_keeps_ties_in_place():
    chunks = [(1, "a", 1), (2, "a", 2), (3, "a", 3)]
    texts = ["gaz", "silo silo", "gaz"]
    assert rerank(SiloFirst(), "?", chunks, texts) == [(2, "a", 2), (1, "a", 1), (3, "a", 3)]


def test_reranked_retrieval_reads_the_chunk_inputs(conn):
    texts = ["Couper le gaz du local.", "Le silo et le gaz du silo."]
    pages = tuple(Page(i, t, "text_layer", 30, 0, 0, 1.0) for i, t in enumerate(texts, start=1))
    store_document(conn, SourceDocument("GDO-test.pdf", "a" * 64, texts, pages))
    candidates = lambda question: _fulltext_chunks(conn, question, 50)
    assert reranked(conn, candidates, SiloFirst())("gaz")[0] == ("GDO-test.pdf", 2)
