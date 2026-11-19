"""Web API and page (J5): one question in, one checked answer out, with its sources.

    uv run uvicorn --factory secours_tech.api:create_app

`POST /api/ask` runs the J4 answer (retrieval, generation, citation checks, verification) and
returns it as JSON; `GET /` serves a page that shows it as plain text, never as HTML. Every
question is logged in `queries` without an IP address or a user identifier and deleted after
90 days.

Guards (docs/architecture.md), from the cheapest:

- a request body above 2 KB, without a length or sent in chunks, is refused before it is read;
  a question has 1 to 500 characters;
- each client (its IP address as uvicorn sees it) may ask 5 questions a minute and 50 a day;
  uvicorn runs with `--no-proxy-headers`, since by default it takes the address from an
  `X-Forwarded-For` header sent through 127.0.0.1, which any local client can write; behind a
  proxy, it must trust that proxy alone (`--forwarded-allow-ips=<its address>`);
- once the day's questions have used `DAILY_TOKENS` Mistral chat tokens (1,000,000 by default,
  a few euros at most), new questions are refused until midnight UTC and a warning is logged;
  the questions already running finish, so the cap can be passed by a few questions;
- a question gets `ASK_SECONDS` (90) for its Mistral calls, retries and waits included; past
  them it fails with a 502 instead of holding a worker for minutes;
- the service refuses to start without the Mistral key, the database or the vectors of every
  current chunk: there is no degraded mode.

The page and the API have no login of their own: in production they sit behind the identity
proxy of the cluster (D25) and must never be exposed without it.
"""

import logging
import math
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from secours_tech.answer import (
    GENERATION_MODEL,
    MAX_QUESTION,
    Answer,
    Chat,
    answer,
    as_json,
    problem_kind,
    retrieve,
)
from secours_tech.env import setting

log = logging.getLogger(__name__)

WEB = Path(__file__).parent / "web"
MAX_BODY = 2048  # bytes; a question of 500 characters fits several times over
LIMITS = ((5, 60.0), (50, 86400.0))  # (questions, seconds) per client
DAILY_TOKENS = 1_000_000  # Mistral tokens a day, every client together; DAILY_TOKENS overrides
RETENTION = "90 days"  # questions may hold personal data typed by a user
CHAT_TIMEOUT = 60  # seconds per Mistral call, retries apart
ASK_SECONDS = 90  # for every Mistral call of one question, retries and waits included
STATEMENT_MS = 10_000  # no query of the API runs longer
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

Connect = Callable[[], AbstractContextManager]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)


class Limiter:
    """Sliding windows per client, in memory: one API process, as deployed."""

    def __init__(self, limits: Sequence[tuple[int, float]] = LIMITS, clock=time.monotonic):
        self.limits = limits
        self.clock = clock
        self.seen: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def wait(self, client: str) -> float:
        """0 when `client` may ask now (the question is counted); else the seconds to wait."""
        now, longest = self.clock(), max(seconds for _, seconds in self.limits)
        with self.lock:
            if len(self.seen) > 10_000:  # forget the clients whose window is over
                self.seen = {k: v for k, v in self.seen.items() if v and now - v[-1] < longest}
            times = self.seen.setdefault(client, deque())
            while times and now - times[0] >= longest:
                times.popleft()
            for count, seconds in self.limits:
                recent = [t for t in times if now - t < seconds]
                if len(recent) >= count:
                    return recent[-count] + seconds - now
            times.append(now)
            return 0.0


def tokens_today(conn) -> int:
    row = conn.execute(
        "SELECT coalesce(sum(prompt_tokens + completion_tokens), 0) FROM queries"
        " WHERE asked_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
    ).fetchone()
    return int(row[0]) if row else 0


def log_query(
    conn,
    question: str,
    result: Answer | None,
    top: float | None,
    seconds: float,
    tokens: Counter[str],
) -> None:
    """One row of `queries` (`result` None: the question failed), and the rows past the
    retention period deleted. `tokens` are those of every chat call made, failed answer or not."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO queries (question, status, problems, pages, top_similarity,"
            " prompt_tokens, completion_tokens, seconds) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                question,
                result.status if result else "error",
                sorted({problem_kind(p) for p in result.problems}) if result else [],
                sorted(
                    {
                        f"{c.passage.document}#{c.passage.page}"
                        for s in result.sentences
                        for c in s.citations
                    }
                )
                if result
                else [],
                top,
                tokens["prompt_tokens"],
                tokens["completion_tokens"],
                round(seconds, 2),
            ),
        )
        conn.execute("DELETE FROM queries WHERE asked_at < now() - %s::interval", (RETENTION,))


def _refuse(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=HEADERS)


def _production() -> tuple[Connect, object, Chat]:
    """The real database, embedder and chat client; refuses to start when one is missing."""
    from secours_tech.db import connect
    from secours_tech.embed import MistralEmbedder, missing_inputs
    from secours_tech.mistral import MistralError, api_key, post

    try:
        key = api_key()
    except MistralError as error:
        raise SystemExit(str(error)) from None
    with connect() as conn:
        if absent := len(missing_inputs(conn, MistralEmbedder.name)):
            raise SystemExit(f"{absent} chunk inputs have no {MistralEmbedder.name} vector")
    return (
        lambda: connect(statement_timeout=STATEMENT_MS),
        MistralEmbedder(key),
        lambda payload: post("chat/completions", payload, key, timeout=CHAT_TIMEOUT),
    )


def create_app(
    connect: Connect | None = None,
    embedder=None,
    chat: Chat | None = None,
    daily_tokens: int | None = None,
    limiter: Limiter | None = None,
) -> FastAPI:
    """The application; without arguments, wired to the real database and Mistral."""
    from secours_tech.mistral import DEADLINE, MistralError

    if connect is None or embedder is None or chat is None:
        connect, embedder, chat = _production()
    if daily_tokens is None:
        daily_tokens = int(setting("DAILY_TOKENS") or DAILY_TOKENS)
    limiter = limiter or Limiter()
    open_db, embed, generate = connect, embedder, chat
    app = FastAPI(title="SecoursTech", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method == "POST":
            if "transfer-encoding" in request.headers:  # its body would be read past the length
                return _refuse(411, "La longueur de la requête est requise.")
            length = request.headers.get("content-length", "")
            if not length.isdigit():
                return _refuse(411, "La longueur de la requête est requise.")
            if int(length) > MAX_BODY:
                return _refuse(413, "La requête est trop longue.")
        response: Response = await call_next(request)
        response.headers.update(HEADERS)
        return response

    @app.get("/health")
    def health() -> dict:
        try:
            with open_db() as conn:
                conn.execute("SELECT 1")
        except Exception:
            log.exception("health: database unreachable")
            raise HTTPException(503, "database unreachable") from None
        return {"status": "ok", "generation": GENERATION_MODEL, "embedding": embed.name}

    @app.post("/api/ask")
    def ask(body: Question, request: Request) -> dict:
        question = " ".join(body.question.split())
        if not question:
            raise HTTPException(422, "La question est vide.")
        client = request.client.host if request.client else "unknown"
        if (wait := limiter.wait(client)) > 0:
            raise HTTPException(
                429,
                "Trop de questions : réessayez plus tard.",
                headers={"Retry-After": str(math.ceil(wait))},
            )
        started = time.perf_counter()
        with open_db() as conn:
            if tokens_today(conn) >= daily_tokens:
                log.warning("daily token cap of %d reached: questions refused", daily_tokens)
                raise HTTPException(503, "Le plafond de questions du jour est atteint.")
            top: float | None = None
            spent = Counter[str]()

            def counted(payload: dict) -> dict:
                """The chat call, its tokens counted even if a later call fails."""
                reply = generate(payload)
                usage = reply.get("usage") or {}
                spent.update(
                    {k: int(usage.get(k, 0)) for k in ("prompt_tokens", "completion_tokens")}
                )
                return reply

            deadline = DEADLINE.set(time.monotonic() + ASK_SECONDS)
            try:
                passages = retrieve(conn, embed, question)
                top = passages[0].similarity if passages else None
                result = answer(question, passages, counted)
            except Exception as error:
                log_query(conn, question, None, top, time.perf_counter() - started, spent)
                if isinstance(error, MistralError):
                    log.warning("ask: %s", error)
                    raise HTTPException(
                        502, "Le service de rédaction ne répond pas : réessayez plus tard."
                    ) from None
                log.exception("ask: the answer failed")
                raise HTTPException(500, "La question n'a pas pu être traitée.") from None
            finally:
                DEADLINE.reset(deadline)
            log_query(conn, question, result, top, time.perf_counter() - started, spent)
        return as_json(result)

    @app.exception_handler(Exception)
    async def failed(request: Request, error: Exception) -> JSONResponse:
        """Any other failure (the database down, say): a plain 500 with the same headers."""
        log.error("unhandled error on %s", request.url.path, exc_info=error)
        return _refuse(500, "La question n'a pas pu être traitée.")

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
    return app
