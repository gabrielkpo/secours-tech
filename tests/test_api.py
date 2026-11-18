"""Web API: the guards before any call, the checked answer as JSON, the query log without IP.
The chat client and the embedder are fakes; nothing leaves the machine."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from secours_tech import api as api_module
from secours_tech.answer import answer
from secours_tech.api import HEADERS, Limiter, as_json, create_app
from secours_tech.embed import embed_corpus
from secours_tech.mistral import MistralError
from test_answer import GOOD, PASSAGES, FakeChat, _draft
from test_embed import FakeEmbedder, _guide

GAS = "Couper le gaz avant de ventiler."
DRAFT = _draft({"text": "Le gaz est coupé.", "citations": [{"passage": "P1", "quote": GAS}]})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_limiter_counts_each_window_and_says_how_long_to_wait():
    clock = Clock()
    limiter = Limiter(((2, 60.0), (3, 3600.0)), clock)
    assert limiter.wait("a") == limiter.wait("a") == 0
    assert limiter.wait("a") == 60  # third in a minute
    assert limiter.wait("b") == 0  # another client has its own windows
    clock.now += 61
    assert limiter.wait("a") == 0  # third in the hour
    assert limiter.wait("a") == pytest.approx(3600 - 61)


def test_the_json_answer_numbers_sources_and_keeps_their_quotes():
    data = as_json(answer("Que faire ?", PASSAGES, FakeChat(_draft(GOOD))))
    assert data["status"] == "answered"
    assert data["sentences"] == [
        {"text": "Le gaz est coupé avant de ventiler, à 50 m de la fuite.", "sources": [1]}
    ]
    (source,) = data["sources"]
    assert source["link"] == "https://example.org/GDO-test.pdf#page=11"
    assert source["quotes"] == [
        "Couper le gaz au robinet de coupure avant toute ventilation",
        "à 50 m du point de fuite",
    ]
    assert data["notice"].endswith("le COS décide.")


def test_a_refusal_lists_the_closest_pages_and_no_sentence():
    data = as_json(answer("Que faire ?", PASSAGES, FakeChat(_draft(status="refusal"))))
    assert (data["status"], data["sentences"], data["sources"]) == ("refused", [], [])
    assert [p["page"] for p in data["pages"]] == [11, 12]


@pytest.fixture
def indexed(conn):
    _guide(conn)
    embed_corpus(conn, FakeEmbedder())
    return conn


def _client(conn, chat=None, **options) -> TestClient:
    @contextmanager
    def connect():
        yield conn

    app = create_app(connect, FakeEmbedder(), chat or FakeChat(DRAFT), **options)
    return TestClient(app)


def test_a_question_gets_a_checked_answer_and_one_log_row_without_ip(indexed):
    response = _client(indexed).post("/api/ask", json={"question": "  Et   le gaz ?  "})
    assert response.status_code == 200
    assert response.json()["sources"][0]["quotes"] == [GAS]
    for name, value in HEADERS.items():
        assert response.headers[name] == value
    row = indexed.execute(
        "SELECT question, status, pages, prompt_tokens, completion_tokens FROM queries"
    ).fetchone()
    assert row == ("Et le gaz ?", "answered", ["GDO-test.pdf#2"], 200, 40)
    columns = {
        r[0]
        for r in indexed.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'queries'"
        )
    }
    assert not columns & {"ip", "client", "user", "email", "session"}


def test_bodies_are_bounded_before_they_are_read(indexed):
    client = _client(indexed)
    assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/ask", json={"question": "   "}).status_code == 422
    big = client.post(
        "/api/ask",
        content=b'{"question": "' + b"x" * 3000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert big.status_code == 413
    chunked = client.post(
        "/api/ask",
        content=iter([b'{"question": "gaz"}']),
        headers={"Content-Type": "application/json"},
    )
    assert chunked.status_code == 411
    assert indexed.execute("SELECT count(*) FROM queries").fetchone()[0] == 0


def test_a_client_over_its_limit_waits_and_costs_nothing(indexed):
    chat = FakeChat(DRAFT)
    client = _client(indexed, chat, limiter=Limiter(((1, 60.0),)))
    assert client.post("/api/ask", json={"question": "gaz ?"}).status_code == 200
    calls = len(chat.payloads)
    refused = client.post("/api/ask", json={"question": "gaz ?"})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) == 60
    assert len(chat.payloads) == calls


def test_the_daily_token_cap_stops_every_question(indexed, caplog):
    indexed.execute(
        "INSERT INTO queries (question, status, prompt_tokens, completion_tokens, seconds)"
        " VALUES ('earlier', 'answered', 900, 100, 1)"
    )
    chat = FakeChat(DRAFT)
    response = _client(indexed, chat, daily_tokens=1000).post(
        "/api/ask", json={"question": "gaz ?"}
    )
    assert response.status_code == 503
    assert chat.payloads == []
    assert "daily token cap" in caplog.text


def test_yesterdays_tokens_do_not_count_and_old_questions_are_deleted(indexed):
    indexed.execute(
        "INSERT INTO queries (asked_at, question, status, prompt_tokens, seconds) VALUES"
        " (now() - interval '1 day 1 hour', 'yesterday', 'answered', 5000, 1),"
        " (now() - interval '91 days', 'too old', 'answered', 0, 1)"
    )
    response = _client(indexed, daily_tokens=1000).post("/api/ask", json={"question": "gaz ?"})
    assert response.status_code == 200
    kept = {r[0] for r in indexed.execute("SELECT question FROM queries")}
    assert kept == {"yesterday", "gaz ?"}


def test_a_mistral_failure_is_a_502_without_details_and_is_logged(indexed):
    def down(payload):
        raise MistralError("chat/completions: HTTP 500 b'secret details'")

    response = _client(indexed, down).post("/api/ask", json={"question": "gaz ?"})
    assert response.status_code == 502
    assert "secret" not in response.text
    status, similarity = indexed.execute("SELECT status, top_similarity FROM queries").fetchone()
    assert status == "error" and similarity is not None


def test_the_page_its_script_and_health_are_served(indexed):
    client = _client(indexed)
    page = client.get("/")
    assert page.status_code == 200 and "/static/app.js" in page.text
    assert "default-src 'none'" in page.headers["Content-Security-Policy"]
    script = client.get("/static/app.js")
    assert script.status_code == 200 and "innerHTML" not in script.text
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 404


def test_the_service_refuses_to_start_without_its_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="MISTRAL_API_KEY"):
        create_app()


def test_the_service_refuses_to_start_with_chunks_left_unembedded(conn, monkeypatch):
    _guide(conn)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", conn.info.dsn)
    with pytest.raises(SystemExit, match="no mistral-embed"):
        api_module._production()
