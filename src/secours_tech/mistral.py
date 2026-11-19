"""Minimal Mistral API client: the key, one JSON call, retries on rate limits.

The key is read from the environment (`MISTRAL_API_KEY`), or from `.env` in the working
directory when the variable is not set. It is sent only to api.mistral.ai and never logged: the
client follows no redirect, since a redirected request would carry the key to another host.
The standard library is enough here; the official SDK would add a dependency for one call.

A caller that must answer in bounded time (the web API) sets `DEADLINE`: every call made in that
context, retries and waits included, gives up with a `MistralError` once it is passed.
"""

import http.client
import json
import random
import re
import time
import urllib.error
import urllib.request
from contextvars import ContextVar
from pathlib import Path

from secours_tech.env import setting

API = "https://api.mistral.ai/v1"
RETRIES = 5
DEADLINE: ContextVar[float | None] = ContextVar("mistral_deadline", default=None)  # monotonic


class MistralError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # the 3xx is raised as an HTTPError instead of being followed


_OPENER = urllib.request.build_opener(_NoRedirect)


def _wait(attempt: int, retry_after: str | None = None) -> float:
    """Seconds before the next attempt: the server's Retry-After when it gives a number of
    seconds, else exponential backoff; plus up to a second of jitter."""
    if retry_after and re.fullmatch(r"[0-9]+", retry_after.strip()):
        return min(int(retry_after), 60) + random.random()  # noqa: S311 - jitter, not a secret
    return 2 ** (attempt + 1) + random.random()  # noqa: S311


def api_key(env_file: Path = Path(".env")) -> str:
    key = setting("MISTRAL_API_KEY", env_file)
    if not key:
        raise MistralError("MISTRAL_API_KEY is not set (environment or .env)")
    return key


def _left(path: str) -> float | None:
    """Seconds left before the context's deadline (None: no deadline); raises once it is past."""
    deadline = DEADLINE.get()
    if deadline is None:
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        raise MistralError(f"{path}: time limit reached")
    return left


def _pause(path: str, seconds: float) -> None:
    """Sleep before a retry, unless the deadline would pass first."""
    left = _left(path)
    if left is not None and seconds >= left:
        raise MistralError(f"{path}: time limit reached")
    time.sleep(seconds)


def post(path: str, payload: dict, key: str, timeout: float = 120) -> dict:
    """POST JSON to the API; waits and retries on the rate limit (429), a 5xx or a network error,
    within the context's `DEADLINE` when one is set."""
    request = urllib.request.Request(  # noqa: S310 - API is a constant https URL
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(RETRIES):
        left = _left(path)
        try:
            with _OPENER.open(request, timeout=min(timeout, left or timeout)) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                raise MistralError(f"{path}: HTTP {error.code} redirect refused") from None
            retryable = error.code == 429 or error.code >= 500
            if retryable and attempt < RETRIES - 1:
                _pause(path, _wait(attempt, error.headers.get("Retry-After")))
                continue
            # the body explains the refusal; the request (and its key) is never echoed
            raise MistralError(f"{path}: HTTP {error.code} {error.read()[:300]!r}") from None
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,  # a truncated body (IncompleteRead)
            json.JSONDecodeError,
        ) as error:
            if attempt < RETRIES - 1:
                _pause(path, _wait(attempt))
                continue
            raise MistralError(f"{path}: network error {type(error).__name__}") from None
    raise MistralError(f"{path}: no answer after {RETRIES} attempts")
